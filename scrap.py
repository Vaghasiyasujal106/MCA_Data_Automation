import os
import re
import time
import pdfplumber
import mysql.connector
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

start_srn_number = 3322047
srn_prefix = "O"
num_iterations = 1000
download_dir = os.path.abspath("downloads")
os.makedirs(download_dir, exist_ok=True)

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',
    'database': 'mca_data'
}
conn = mysql.connector.connect(**db_config)
cursor = conn.cursor()

chrome_options = Options()
chrome_options.add_experimental_option("prefs", {
    "download.default_directory": download_dir,
    "plugins.always_open_pdf_externally": True,
    "download.prompt_for_download": False
})
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--log-level=3")

def start_driver():
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def extract(pattern, text, default="", flags=0):
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else default

driver = start_driver()
wait = WebDriverWait(driver, 20)

for i in range(num_iterations):
    srn_value = f"{srn_prefix}{start_srn_number + i:08d}"
    expected_pdf_path = os.path.join(download_dir, f"{srn_value}.pdf")

    for f in os.listdir(download_dir):
        if f.endswith(".pdf"):
            os.remove(os.path.join(download_dir, f))

    print(f"\n Checking SRN: {srn_value}")

    try:
        driver.get("https://www.mca.gov.in/mcafoportal/trackPaymentStatus.do")
        time.sleep(2)

        try:
            driver.find_element(By.ID, "msgboxclose").click()
        except:
            pass

        srn_input = wait.until(EC.presence_of_element_located((By.ID, "srn")))
        srn_input.clear()
        srn_input.send_keys(srn_value)
        wait.until(EC.element_to_be_clickable((By.ID, "trackPaymentStatus_0"))).click()
        time.sleep(3)

        if "Invalid SRN" in driver.page_source:
            print(f" Skipping Invalid SRN: {srn_value}")
            continue

        challan_btn = wait.until(EC.element_to_be_clickable((By.ID, "screen_button_ViewFormChallan")))
        challan_btn.click()
        time.sleep(5)

        tabs = driver.window_handles
        driver.switch_to.window(tabs[-1])

        downloaded_file = os.path.join(download_dir, "displayChallanReceipt.pdf")
        for _ in range(30):
            if os.path.exists(downloaded_file):
                os.rename(downloaded_file, expected_pdf_path)
                break
            time.sleep(1)
        else:
            print(f" PDF not downloaded for {srn_value}")
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
            continue

        with pdfplumber.open(expected_pdf_path) as pdf:
            text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])

        text_cleaned = text.replace('\r', '').replace('\n', '\n')

        srn = extract(r"(O\d{8})", text)
        srn_date = extract(r"Service Request Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)
        expiry_date = extract(r"Expiry Date\s*:\s*(\d{2}/\d{2}/\d{4})", text)

        name = extract(r"(?:Received From|By whom tendered)\s*:\s*(.*?)\n", text)

        address_match = re.search(r"Address\s*:\s*((?:.*\n){1,4})", text)
        address = address_match.group(1).replace('\n', ', ').strip() if address_match else ""

        service_match = re.search(
            r"Service Description\s*:([\s\S]*?)(?:\n(?:Type of Fee|Amount|Head of Account|Rupees|Total|Accounts))",
            text_cleaned,
            flags=re.IGNORECASE
        )
        if service_match:
            service_description = service_match.group(1)
            service_description = ' '.join(service_description.split())
        else:
            service_description = ""

        status = "Paid" if srn_date else ("Not Paid" if expiry_date else "Unknown")

        query = """
        INSERT INTO challan_data (srn, status, srn_date, expiry_date, name, address, service_description, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status=VALUES(status),
            srn_date=VALUES(srn_date),
            expiry_date=VALUES(expiry_date),
            name=VALUES(name),
            address=VALUES(address),
            service_description=VALUES(service_description),
            created_at=VALUES(created_at)
        """
        cursor.execute(query, (
            srn,
            status,
            datetime.strptime(srn_date, "%d/%m/%Y").date() if srn_date else None,
            datetime.strptime(expiry_date, "%d/%m/%Y").date() if expiry_date else None,
            name,
            address,
            service_description,
            datetime.now()
        ))
        conn.commit()
        print(f" Saved: {srn} | Status: {status} | Description: {service_description[:50]}...")

        driver.close()
        driver.switch_to.window(driver.window_handles[0])

    except Exception as e:
        print(f" Error on SRN {srn_value}: {e}")
        continue

driver.quit()
cursor.close()
conn.close()
print("\n All SRNs processed successfully.")
