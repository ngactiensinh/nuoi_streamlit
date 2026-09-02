"""
Script tự động đánh thức các app Streamlit đang ngủ đông.
Dùng Selenium để phát hiện và bấm nút "Yes, get this app back up!"
"""

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

# Danh sách tất cả app cần keep alive
STREAMLIT_APPS = [
    "https://congvieccanhan.streamlit.app/",
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

def create_driver():
    """Khởi tạo Chrome headless driver."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def wake_app(driver, url):
    """
    Truy cập app, kiểm tra xem có đang ngủ không.
    Nếu có nút wake-up thì bấm. Trả về trạng thái.
    """
    print(f"\n🔍 Đang kiểm tra: {url}")
    try:
        driver.get(url)

        # Chờ tối đa 15 giây để trang load
        time.sleep(5)

        # Tìm nút "Yes, get this app back up!" theo nhiều cách
        wake_button = None

        # Cách 1: Tìm theo text chứa "get this app back up"
        try:
            wake_button = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//*[contains(text(), 'get this app back up')]")
                )
            )
        except TimeoutException:
            pass

        # Cách 2: Tìm theo text "Yes"
        if not wake_button:
            try:
                wake_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(text(), 'Yes')]")
                    )
                )
            except TimeoutException:
                pass

        if wake_button:
            wake_button.click()
            print(f"  ✅ Đã bấm nút đánh thức! Chờ app khởi động...")
            time.sleep(10)  # Chờ app load sau khi đánh thức
            print(f"  🟢 App đã được đánh thức: {url}")
            return "woken"
        else:
            print(f"  💤 App đang hoạt động bình thường (không cần đánh thức)")
            return "already_awake"

    except Exception as e:
        print(f"  ❌ Lỗi khi xử lý {url}: {e}")
        return "error"


def main():
    print("=" * 60)
    print("  🚀 BẮT ĐẦU KIỂM TRA VÀ ĐÁNH THỨC CÁC APP STREAMLIT")
    print("=" * 60)

    driver = create_driver()
    results = {"already_awake": 0, "woken": 0, "error": 0}

    try:
        for url in STREAMLIT_APPS:
            status = wake_app(driver, url)
            results[status] += 1
            time.sleep(2)  # Nghỉ giữa các app
    finally:
        driver.quit()

    print("\n" + "=" * 60)
    print("  📊 KẾT QUẢ:")
    print(f"  ✅ Đang hoạt động bình thường : {results['already_awake']} app")
    print(f"  🟢 Đã đánh thức thành công    : {results['woken']} app")
    print(f"  ❌ Lỗi                         : {results['error']} app")
    print("=" * 60)


if __name__ == "__main__":
    main()
