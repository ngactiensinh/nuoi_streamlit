"""
Script tự động đánh thức các app Streamlit Community Cloud đang ngủ đông.

Phiên bản 2 — sửa lỗi "app vẫn ngủ dù script có chạy":
- Không còn mặc định "không thấy nút = app đang chạy". Script chỉ coi app là
  đang chạy khi thực sự thấy giao diện Streamlit (data-testid="stApp").
- Tìm nút đánh thức theo nhiều cách, cả trong trang chính lẫn trong iframe.
- Sau khi bấm, chờ tới khi app lên thật (tối đa 3 phút) rồi mới báo thành công.
- Ở lại mỗi app một lúc để Streamlit ghi nhận có lượt truy cập.
- Chụp màn hình các app có vấn đề (thư mục screenshots/) để xem lại trên GitHub.
- Mỗi app dùng một trình duyệt riêng, lỗi app này không kéo theo app khác.
- Có app lỗi/vẫn ngủ thì thoát mã 1 để GitHub báo đỏ và gửi email.
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# ---------------------------------------------------------------------------
# Danh sách app cần giữ thức
# ---------------------------------------------------------------------------
STREAMLIT_APPS = [
    "https://congvieccanhan.streamlit.app/",
    "https://thuvien-python-ngacvantuan.streamlit.app/",
    "https://chatbot-nghiquyet-xiv.streamlit.app/",
    "https://ngacvantuanbantivi.streamlit.app/",
    "https://bantinchibo.streamlit.app/",
    "https://bao-cao-tgdv.streamlit.app/",
    "https://tracuuluong-tgdvtq.streamlit.app/",
    "https://dangkytinbaitgdv.streamlit.app/",
    "https://diemtinhangngaytgdv.streamlit.app/",
    "https://hesinhthaitgdv.streamlit.app/",
    "https://quan-ly-ho-so-tgdv.streamlit.app/",
    "https://tailieuhopbtgdv.streamlit.app/",
    "https://theodoinangluongbtgdv.streamlit.app/",
    "https://thongketruycap.streamlit.app/",
    "https://tomtatvanban.streamlit.app/",
    "https://tomtattinbai.streamlit.app/",
    "https://tgdv-miniapp-app.streamlit.app/",
]

# ---------------------------------------------------------------------------
# Cấu hình thời gian (giây)
# ---------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT = 90   # tối đa chờ tải trang
DETECT_TIMEOUT = 60      # tối đa chờ để nhận ra trạng thái ban đầu của app
WAKE_TIMEOUT = 180       # tối đa chờ app khởi động sau khi bấm đánh thức
STAY_SECONDS = 15        # ở lại app đang chạy để Streamlit tính là có truy cập
POLL_INTERVAL = 5
RETRY_DELAY = 10

SCREENSHOT_DIR = Path("screenshots")

# Dấu hiệu nhận biết trạng thái (so khớp chữ thường)
SLEEP_MARKERS = ["get this app back up", "gone to sleep", "zzzz"]
BOOT_MARKERS = ["in the oven", "spinning up", "waking up", "is starting", "restarting"]
ERROR_MARKERS = ["oh no.", "error running app", "has encountered an error"]

# Các cách tìm nút "Yes, get this app back up!"
WAKE_XPATHS = [
    "//button[contains(normalize-space(.), 'get this app back up')]",
    "//*[@data-testid and contains(@data-testid, 'wakeup')]",
    "//*[@role='button' and contains(normalize-space(.), 'get this app back up')]",
    "//*[contains(normalize-space(text()), 'get this app back up')]",
]

# Kết quả nào tính là thất bại (làm workflow báo đỏ)
FAIL_STATUSES = {"still_sleeping", "app_error", "error", "unknown"}

STATUS_LABELS = {
    "already_awake": "✅ Đang chạy",
    "woken": "🟢 Đã đánh thức",
    "booting": "⏳ Đang khởi động (chưa xong)",
    "still_sleeping": "💤 Vẫn ngủ",
    "app_error": "🟥 App báo lỗi",
    "error": "❌ Lỗi trình duyệt/mạng",
    "unknown": "❓ Không xác định",
}

VN_TZ = timezone(timedelta(hours=7))


# ---------------------------------------------------------------------------
# Trình duyệt
# ---------------------------------------------------------------------------
def create_driver():
    """Khởi tạo Chrome headless. Dùng đường dẫn từ biến môi trường nếu có."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument("--disable-blink-features=AutomationControlled")

    chrome_path = os.environ.get("CHROME_PATH")
    if chrome_path:
        options.binary_location = chrome_path

    driver_path = os.environ.get("CHROMEDRIVER_PATH")
    if driver_path:
        driver = webdriver.Chrome(service=Service(driver_path), options=options)
    else:
        driver = webdriver.Chrome(options=options)  # Selenium Manager tự lo driver

    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def safe_default(driver):
    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass


def frame_count(driver):
    try:
        return len(driver.find_elements(By.TAG_NAME, "iframe"))
    except WebDriverException:
        return 0


# ---------------------------------------------------------------------------
# Đọc trạng thái trang
# ---------------------------------------------------------------------------
def collect_text(driver):
    """Lấy toàn bộ chữ hiển thị ở trang chính và các iframe (chữ thường)."""
    js = "return document.body ? document.body.innerText : '';"
    parts = []
    safe_default(driver)
    try:
        parts.append(driver.execute_script(js) or "")
    except WebDriverException:
        pass

    for i in range(frame_count(driver)):
        try:
            driver.switch_to.frame(i)
            parts.append(driver.execute_script(js) or "")
        except WebDriverException:
            pass
        finally:
            safe_default(driver)

    return "\n".join(parts).lower()


def has_running_app(driver):
    """True nếu thấy giao diện Streamlit thật (stApp) ở trang chính hoặc iframe."""
    selector = '[data-testid="stApp"], [data-testid="stAppViewContainer"]'
    safe_default(driver)
    try:
        if driver.find_elements(By.CSS_SELECTOR, selector):
            return True
    except WebDriverException:
        pass

    for i in range(frame_count(driver)):
        try:
            driver.switch_to.frame(i)
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        except WebDriverException:
            pass
        finally:
            safe_default(driver)
    return False


def detect_state(driver):
    """Trả về: running / sleeping / booting / error_page / unknown."""
    text = collect_text(driver)
    if any(m in text for m in SLEEP_MARKERS):
        return "sleeping"
    if has_running_app(driver):
        return "running"
    if any(m in text for m in ERROR_MARKERS):
        return "error_page"
    if any(m in text for m in BOOT_MARKERS):
        return "booting"
    return "unknown"


def wait_for_state(driver, timeout, stop_states):
    """Hỏi trạng thái định kỳ tới khi rơi vào stop_states hoặc hết giờ."""
    deadline = time.time() + timeout
    state = detect_state(driver)
    while state not in stop_states and time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        state = detect_state(driver)
    return state


# ---------------------------------------------------------------------------
# Bấm nút đánh thức
# ---------------------------------------------------------------------------
def _click_in_current_context(driver):
    for xpath in WAKE_XPATHS:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
        except WebDriverException:
            continue
        for el in elements:
            try:
                if not el.is_displayed():
                    continue
                try:
                    el.click()
                except WebDriverException:
                    driver.execute_script("arguments[0].click();", el)
                return True
            except WebDriverException:
                continue
    return False


def click_wake_button(driver):
    """Tìm và bấm nút đánh thức ở trang chính, rồi tới từng iframe."""
    safe_default(driver)
    if _click_in_current_context(driver):
        return True

    for i in range(frame_count(driver)):
        try:
            driver.switch_to.frame(i)
            if _click_in_current_context(driver):
                safe_default(driver)
                return True
        except WebDriverException:
            pass
        finally:
            safe_default(driver)
    return False


# ---------------------------------------------------------------------------
# Xử lý từng app
# ---------------------------------------------------------------------------
def app_name(url):
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return host.replace(".streamlit.app", "")


def screenshot(driver, url, tag):
    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        path = SCREENSHOT_DIR / f"{app_name(url)}_{tag}.png"
        safe_default(driver)
        driver.save_screenshot(str(path))
        print(f"  📸 Đã chụp màn hình: {path}")
    except WebDriverException:
        pass


def stay_on_app(driver):
    """Ở lại app một lúc để Streamlit ghi nhận lượt truy cập."""
    time.sleep(STAY_SECONDS)


def process_app(url, attempt):
    print(f"\n🔍 [{attempt}] Đang kiểm tra: {url}")
    driver = None
    try:
        driver = create_driver()
        driver.get(url)

        state = wait_for_state(
            driver, DETECT_TIMEOUT, {"running", "sleeping", "error_page"}
        )
        print(f"  • Trạng thái ban đầu: {state}")

        if state == "running":
            stay_on_app(driver)
            print("  ✅ App đang chạy")
            return "already_awake"

        if state == "error_page":
            screenshot(driver, url, "app_error")
            print("  🟥 App đang báo lỗi (cần vào Streamlit Cloud xem log)")
            return "app_error"

        if state == "sleeping":
            if not click_wake_button(driver):
                screenshot(driver, url, "no_button")
                print("  💤 App đang ngủ nhưng KHÔNG tìm thấy nút đánh thức")
                return "still_sleeping"
            print("  👆 Đã bấm nút đánh thức, chờ app khởi động...")
            time.sleep(POLL_INTERVAL)

        # Sau khi bấm (hoặc trạng thái ban đầu là booting/unknown): chờ app lên
        state = wait_for_state(driver, WAKE_TIMEOUT, {"running", "error_page"})

        if state == "running":
            stay_on_app(driver)
            print("  🟢 App đã chạy")
            return "woken"

        screenshot(driver, url, state)
        if state == "error_page":
            print("  🟥 App khởi động bị lỗi")
            return "app_error"
        if state == "sleeping":
            print("  💤 Bấm rồi nhưng app vẫn ngủ")
            return "still_sleeping"
        if state == "booting":
            print("  ⏳ App vẫn đang khởi động sau thời gian chờ")
            return "booting"
        print("  ❓ Không nhận ra trạng thái trang")
        return "unknown"

    except WebDriverException as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        print(f"  ❌ Lỗi trình duyệt: {msg}")
        if driver:
            screenshot(driver, url, "error")
        return "error"
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass


def write_github_summary(rows, counts):
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    now = datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    lines = [
        f"## 🌅 Kết quả đánh thức Streamlit — {now} (giờ VN)",
        "",
        "| App | Kết quả | Số lần thử |",
        "|---|---|---|",
    ]
    for url, status, attempts in rows:
        lines.append(f"| {app_name(url)} | {STATUS_LABELS[status]} | {attempts} |")
    lines.append("")
    lines.append(
        " · ".join(f"{STATUS_LABELS[k]}: **{v}**" for k, v in counts.items() if v)
    )
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    print("=" * 60)
    print("  🚀 BẮT ĐẦU KIỂM TRA VÀ ĐÁNH THỨC CÁC APP STREAMLIT")
    print("=" * 60)

    counts = {k: 0 for k in STATUS_LABELS}
    rows = []

    for url in STREAMLIT_APPS:
        status = process_app(url, 1)
        attempts = 1
        if status in FAIL_STATUSES or status == "booting":
            print(f"  🔁 Thử lại sau {RETRY_DELAY} giây...")
            time.sleep(RETRY_DELAY)
            status = process_app(url, 2)
            attempts = 2
        counts[status] += 1
        rows.append((url, status, attempts))

    print("\n" + "=" * 60)
    print("  📊 KẾT QUẢ:")
    for url, status, attempts in rows:
        print(f"  {STATUS_LABELS[status]:<32} {app_name(url)}  (thử {attempts} lần)")
    print("-" * 60)
    for key, value in counts.items():
        if value:
            print(f"  {STATUS_LABELS[key]:<32}: {value} app")
    print("=" * 60)

    write_github_summary(rows, counts)

    failed = sum(counts[s] for s in FAIL_STATUSES)
    if failed:
        print(f"\n⚠️  Có {failed} app chưa ổn — xem ảnh chụp trong mục Artifacts.")
        sys.exit(1)


if __name__ == "__main__":
    main()
