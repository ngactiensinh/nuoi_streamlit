"""
Script tự động đánh thức các app Streamlit Community Cloud đang ngủ đông.

Phiên bản 3 — sửa lỗi "job chạy quá 1 tiếng bị hủy, app cuối danh sách không
bao giờ được đánh thức" (như tomtattinbai):
- Chạy SONG SONG nhiều app một lúc (mặc định 4), mỗi app một trình duyệt riêng.
- Có ngân sách thời gian tổng (mặc định 45 phút): gần hết giờ thì bỏ lượt thử
  lại để kịp in kết quả, không bao giờ bị GitHub hủy giữa chừng.
- In log ngay lập tức (không bị giữ trong bộ đệm), log của từng app in thành
  một khối liền, không lẫn vào nhau.
- Nhận biết thêm trang "vượt giới hạn tài nguyên" (cần Reboot thủ công) và
  trang "không có quyền / app không tồn tại".

Giữ nguyên từ bản 2:
- Chỉ coi app là đang chạy khi thực sự thấy giao diện Streamlit (stApp).
- Tìm nút đánh thức ở trang chính lẫn trong iframe, bấm xong chờ app lên thật.
- Ở lại mỗi app một lúc để Streamlit ghi nhận có lượt truy cập.
- Chụp màn hình app có vấn đề (thư mục screenshots/).
- Có app lỗi/vẫn ngủ thì thoát mã 1 để GitHub báo đỏ và gửi email.
"""

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# In log ra ngay, không giữ trong bộ đệm
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

# ---------------------------------------------------------------------------
# Danh sách app cần giữ thức
# ---------------------------------------------------------------------------
STREAMLIT_APPS = [
    "https://congvieccanhan.streamlit.app/",
    "https://thuvien-python-ngacvantuan.streamlit.app/",
    "https://bantinchibo.streamlit.app/",
    "https://tracuuluong-tgdvtq.streamlit.app/",
    "https://dangkytinbaitgdv.streamlit.app/",
    "https://diemtinhangngaytgdv.streamlit.app/",
    "https://hesinhthaitgdv.streamlit.app/",
    "https://quan-ly-ho-so-tgdv.streamlit.app/",
    "https://tailieuhopbtgdv.streamlit.app/",
    "https://thongketruycap.streamlit.app/",
    "https://tomtattinbai.streamlit.app/",
    "https://tgdv-miniapp-app.streamlit.app/",
]

# ---------------------------------------------------------------------------
# Cấu hình thời gian (giây)
# ---------------------------------------------------------------------------
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "4"))          # số app chạy cùng lúc
TOTAL_BUDGET = int(os.environ.get("TOTAL_BUDGET", str(45 * 60)))  # ngân sách tổng

PAGE_LOAD_TIMEOUT = 60   # tối đa chờ tải trang
DETECT_TIMEOUT = 45      # tối đa chờ để nhận ra trạng thái ban đầu của app
WAKE_TIMEOUT = 150       # tối đa chờ app khởi động sau khi bấm đánh thức
STAY_SECONDS = 15        # ở lại app đang chạy để Streamlit tính là có truy cập
POLL_INTERVAL = 5
RETRY_DELAY = 10

# Thời gian xấu nhất cho một lượt xử lý một app (dùng để quyết định có thử lại)
APP_WORST_CASE = PAGE_LOAD_TIMEOUT + DETECT_TIMEOUT + WAKE_TIMEOUT + STAY_SECONDS + 30

SCREENSHOT_DIR = Path("screenshots")

# Dấu hiệu nhận biết trạng thái (so khớp chữ thường)
SLEEP_MARKERS = ["get this app back up", "gone to sleep", "zzzz"]
BOOT_MARKERS = ["in the oven", "spinning up", "waking up", "is starting", "restarting"]
ERROR_MARKERS = ["oh no.", "error running app", "has encountered an error"]
LIMIT_MARKERS = ["resource limits", "gone over its resource"]
NO_ACCESS_MARKERS = [
    "do not have access to this app",
    "app does not exist",
    "it does not exist",
    "you do not have access",
]

# Các cách tìm nút "Yes, get this app back up!"
WAKE_XPATHS = [
    "//button[contains(normalize-space(.), 'get this app back up')]",
    "//*[@data-testid and contains(@data-testid, 'wakeup')]",
    "//*[@role='button' and contains(normalize-space(.), 'get this app back up')]",
    "//*[contains(normalize-space(text()), 'get this app back up')]",
]

# Kết quả nào tính là thất bại (làm workflow báo đỏ)
FAIL_STATUSES = {
    "still_sleeping", "app_error", "over_limit", "no_access",
    "error", "unknown", "skipped",
}

STATUS_LABELS = {
    "already_awake": "✅ Đang chạy",
    "woken": "🟢 Đã đánh thức",
    "booting": "⏳ Đang khởi động (chưa xong)",
    "still_sleeping": "💤 Vẫn ngủ",
    "app_error": "🟥 App báo lỗi",
    "over_limit": "🟧 Vượt giới hạn tài nguyên (cần Reboot)",
    "no_access": "🔒 Không truy cập được / sai địa chỉ",
    "error": "❌ Lỗi trình duyệt/mạng",
    "unknown": "❓ Không xác định",
    "skipped": "⏭️ Bỏ qua (hết thời gian)",
}

VN_TZ = timezone(timedelta(hours=7))
START_TIME = time.time()
PRINT_LOCK = threading.Lock()


def time_left():
    return TOTAL_BUDGET - (time.time() - START_TIME)


def elapsed_str():
    sec = int(time.time() - START_TIME)
    return f"{sec // 60:02d}:{sec % 60:02d}"


class AppLog:
    """Gom log của một app, in ra thành một khối khi xong để không bị lẫn."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(msg)

    def flush(self):
        if not self.lines:
            return
        with PRINT_LOCK:
            print("\n".join(self.lines), flush=True)
        self.lines = []


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
    """Trả về: running / sleeping / booting / error_page / over_limit / no_access / unknown."""
    text = collect_text(driver)
    if any(m in text for m in SLEEP_MARKERS):
        return "sleeping"
    if has_running_app(driver):
        return "running"
    if any(m in text for m in LIMIT_MARKERS):
        return "over_limit"
    if any(m in text for m in NO_ACCESS_MARKERS):
        return "no_access"
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


def screenshot(driver, url, tag, log):
    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        path = SCREENSHOT_DIR / f"{app_name(url)}_{tag}.png"
        safe_default(driver)
        driver.save_screenshot(str(path))
        log(f"  📸 Đã chụp màn hình: {path}")
    except WebDriverException:
        pass


FINAL_STATE_TO_STATUS = {
    "error_page": ("app_error", "🟥 App đang báo lỗi (vào Streamlit Cloud xem log)"),
    "over_limit": ("over_limit", "🟧 App vượt giới hạn tài nguyên — vào Manage app → Reboot"),
    "no_access": ("no_access", "🔒 Không truy cập được (app private / sai địa chỉ / đã xóa)"),
}


def process_app(url, attempt, log):
    log(f"\n🔍 [{elapsed_str()}] [lần {attempt}] {url}")
    driver = None
    try:
        driver = create_driver()
        driver.get(url)

        state = wait_for_state(
            driver, DETECT_TIMEOUT,
            {"running", "sleeping", "error_page", "over_limit", "no_access"},
        )
        log(f"  • Trạng thái ban đầu: {state}")

        if state == "running":
            time.sleep(STAY_SECONDS)
            log("  ✅ App đang chạy")
            return "already_awake"

        if state in FINAL_STATE_TO_STATUS:
            status, msg = FINAL_STATE_TO_STATUS[state]
            screenshot(driver, url, status, log)
            log(f"  {msg}")
            return status

        if state == "sleeping":
            if not click_wake_button(driver):
                screenshot(driver, url, "no_button", log)
                log("  💤 App đang ngủ nhưng KHÔNG tìm thấy nút đánh thức")
                return "still_sleeping"
            log("  👆 Đã bấm nút đánh thức, chờ app khởi động...")
            time.sleep(POLL_INTERVAL)

        # Sau khi bấm (hoặc trạng thái ban đầu là booting/unknown): chờ app lên
        state = wait_for_state(
            driver, WAKE_TIMEOUT,
            {"running", "error_page", "over_limit", "no_access"},
        )

        if state == "running":
            time.sleep(STAY_SECONDS)
            log("  🟢 App đã chạy")
            return "woken"

        if state in FINAL_STATE_TO_STATUS:
            status, msg = FINAL_STATE_TO_STATUS[state]
            screenshot(driver, url, status, log)
            log(f"  {msg}")
            return status

        screenshot(driver, url, state, log)
        if state == "sleeping":
            log("  💤 Bấm rồi nhưng app vẫn ngủ")
            return "still_sleeping"
        if state == "booting":
            log("  ⏳ App vẫn đang khởi động sau thời gian chờ")
            return "booting"
        log("  ❓ Không nhận ra trạng thái trang")
        return "unknown"

    except WebDriverException as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"  ❌ Lỗi trình duyệt: {msg}")
        if driver:
            screenshot(driver, url, "error", log)
        return "error"
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass


def handle_app(url):
    """Xử lý một app, có thử lại một lần nếu còn đủ thời gian."""
    log = AppLog()

    if time_left() < APP_WORST_CASE:
        log(f"\n⏭️  Bỏ qua {url} — không còn đủ thời gian")
        log.flush()
        return url, "skipped", 0

    status = process_app(url, 1, log)
    attempts = 1

    # Không thử lại với lỗi cần xử lý thủ công (vượt tài nguyên, không có quyền)
    retryable = status in {"still_sleeping", "app_error", "error", "unknown", "booting"}
    if retryable:
        if time_left() > APP_WORST_CASE + RETRY_DELAY:
            log(f"  🔁 Thử lại sau {RETRY_DELAY} giây...")
            log.flush()
            time.sleep(RETRY_DELAY)
            status = process_app(url, 2, log)
            attempts = 2
        else:
            log("  ⏱️  Không thử lại vì sắp hết thời gian")

    log.flush()
    return url, status, attempts


def write_github_summary(rows, counts):
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    now = datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    lines = [
        f"## 🌅 Kết quả đánh thức Streamlit — {now} (giờ VN)",
        f"Thời gian chạy: {elapsed_str()} · Chạy song song: {MAX_WORKERS} app",
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
    print(f"  {len(STREAMLIT_APPS)} app · song song {MAX_WORKERS} · "
          f"ngân sách {TOTAL_BUDGET // 60} phút")
    print("=" * 60, flush=True)

    counts = {k: 0 for k in STATUS_LABELS}
    results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(handle_app, url): url for url in STREAMLIT_APPS}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                _, status, attempts = fut.result()
            except Exception as e:  # phòng lỗi bất ngờ, không để sập cả lượt
                with PRINT_LOCK:
                    print(f"\n❌ Lỗi không mong đợi với {url}: {e}", flush=True)
                status, attempts = "error", 1
            results[url] = (status, attempts)

    # Giữ đúng thứ tự danh sách khi in kết quả
    rows = []
    for url in STREAMLIT_APPS:
        status, attempts = results[url]
        counts[status] += 1
        rows.append((url, status, attempts))

    print("\n" + "=" * 60)
    print(f"  📊 KẾT QUẢ (chạy hết {elapsed_str()}):")
    for url, status, attempts in rows:
        print(f"  {STATUS_LABELS[status]:<40} {app_name(url)}  (thử {attempts} lần)")
    print("-" * 60)
    for key, value in counts.items():
        if value:
            print(f"  {STATUS_LABELS[key]:<40}: {value} app")
    print("=" * 60, flush=True)

    write_github_summary(rows, counts)

    failed = sum(counts[s] for s in FAIL_STATUSES)
    if failed:
        print(f"\n⚠️  Có {failed} app chưa ổn — xem ảnh chụp trong mục Artifacts.")
        sys.exit(1)


if __name__ == "__main__":
    main()
