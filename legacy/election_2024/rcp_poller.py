import os
import subprocess
import requests
import json
import zipfile
import io
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from twilio.rest import Client
import csv


def get_chrome_version():
    """Get the current installed version of Google Chrome."""
    try:
        version_output = subprocess.check_output(['google-chrome', '--version']).decode('utf-8').strip()
        version = version_output.split()[-1]
        return version
    except subprocess.CalledProcessError:
        raise Exception("Failed to get Chrome version. Ensure Google Chrome is installed and accessible in PATH.")


def fetch_driver_version(chrome_version, json_url):
    """Fetch the correct ChromeDriver version for the installed Chrome version."""
    response = requests.get(json_url)
    response.raise_for_status()
    versions = response.json()['versions']

    for version_info in versions:
        if chrome_version.startswith(version_info['version'].split('.')[0]):
            for download in version_info['downloads'].get('chromedriver', []):
                if download['platform'] == 'linux64':
                    return download['url'], version_info['version']
    raise Exception(f"No matching ChromeDriver version found for Chrome version {chrome_version}")


def download_and_extract_chromedriver(url, version, extract_path=None):
    """Download and extract ChromeDriver if it doesn't already exist."""
    if extract_path is None:
        extract_path = os.path.join(os.getcwd(), 'chromedriver')

    version_file_path = os.path.join(extract_path, 'version.txt')

    # Check if the correct version is already downloaded
    if os.path.exists(extract_path) and os.path.exists(version_file_path):
        with open(version_file_path, 'r') as version_file:
            installed_version = version_file.read().strip()
        if installed_version == version:
            # print(f"ChromeDriver version {version} is already installed.")
            chromedriver_path = os.path.join(extract_path, f'chromedriver-linux64/chromedriver')
            return chromedriver_path

    if not os.path.exists(extract_path):
        os.makedirs(extract_path)

    print(f"Downloading ChromeDriver version {version}...")
    response = requests.get(url)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        z.extractall(extract_path)

    chromedriver_path = os.path.join(extract_path, 'chromedriver-linux64', 'chromedriver')

    if not os.path.exists(chromedriver_path):
        raise FileNotFoundError("The ChromeDriver binary was not found in the extracted files.")

    os.chmod(chromedriver_path, 0o755)

    with open(version_file_path, 'w') as version_file:
        version_file.write(version)

    # print(f"ChromeDriver installed at: {chromedriver_path}")
    return chromedriver_path


def send_whatsapp_alert(message, to_phone_number):
    """Send a WhatsApp message using Twilio."""
    account_sid = ''  # Replace with your Twilio Account SID (Starts with AC)
    api_key = ''  # Replace with your Twilio API Key (Starts with SK)
    api_secret = ''  # Replace with your Twilio API Secret
    from_whatsapp_number = ''  # Twilio Sandbox WhatsApp number

    client = Client(api_key, api_secret, account_sid)

    message = client.messages.create(
        body=message,
        from_=from_whatsapp_number,
        to=f'whatsapp:+{to_phone_number}'
    )

    print(f"WhatsApp alert sent to {to_phone_number}: {message.sid}")


def fetch_poll_data(url):
    """Fetch poll data from the specified URL."""
    chrome_version = get_chrome_version()
    json_url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
    driver_url, driver_version = fetch_driver_version(chrome_version, json_url)

    chromedriver_path = download_and_extract_chromedriver(driver_url, driver_version)

    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--headless")


    # print(f"Using ChromeDriver path: {chromedriver_path}")
    if not os.path.isfile(chromedriver_path):
        raise ValueError(f"The path is not a valid file: {chromedriver_path}")

    driver = webdriver.Chrome(service=Service(chromedriver_path), options=chrome_options)
    driver.get(url)

    # Wait for the table element to be present
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.w-full")))
    except Exception as e:
        driver.quit()
        raise Exception("Table element not found on the page") from e

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    table = soup.find('table', {'class': 'w-full'})

    if not table:
        driver.quit()
        raise ValueError("Could not find the table on the page")

    poll_data = []

    for row in table.find('tbody').find_all('tr'):
        cells = row.find_all('td')
        pollster = cells[0].text.strip()
        date = cells[1].text.strip()
        sample = cells[2].text.strip()
        moe = cells[3].text.strip()
        harris = float(cells[4].text.strip())
        trump = float(cells[5].text.strip())
        spread = cells[6].text.strip()

        poll_data.append({
            'pollster': pollster,
            'date': date,
            'sample': sample,
            'moe': moe,
            'harris': harris,
            'trump': trump,
            'spread': spread
        })

    driver.quit()
    return poll_data


def save_poll_data_to_csv(poll_data, file_path):
    """Save the poll data to a CSV file."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['pollster', 'date', 'sample', 'moe', 'harris', 'trump', 'spread'])
        writer.writeheader()
        writer.writerows(poll_data)



def main():
    url = "https://www.realclearpolling.com/polls/president/general/2024/trump-vs-harris"
    previous_harris = 48.5  # Initial value for Harris from Aug 23
    previous_trump = 46.8  # Initial value for Trump from Aug 23

    while True:
        poll_data = fetch_poll_data(url)

        current_harris = poll_data[0]['harris']
        current_trump = poll_data[0]['trump']

        print(f'Current Harris: {current_harris}')
        print(f'Current Trump: {current_trump}')


        if current_harris != previous_harris or current_trump != previous_trump:
            harris_change = current_harris - previous_harris
            trump_change = current_trump - previous_trump
            harris_percent_change = (harris_change / previous_harris) * 100
            trump_percent_change = (trump_change / previous_trump) * 100


            # Determine which candidate is leading
            if current_harris > current_trump:
                bet_suggestion = "Bet on Harris!"
            else:
                bet_suggestion = "Bet on Trump!"

            message = (
                f"Current RCP Average:\n"
                f"Harris: {current_harris}%\n"
                f"Change: {harris_change:+.2f} ({harris_percent_change:+.2f}%)\n"
                f"Trump: {current_trump}%\n"
                f"Change: {trump_change:+.2f} ({trump_percent_change:+.2f}%)\n\n"
                f"{bet_suggestion}\n"
                f"https://polymarket.com/event/if-rfk-drops-out-who-will-gain-more-in-polls?tid=1725319054766"
            )

            send_whatsapp_alert(message, '14803633331')  # Replace with your phone number

            previous_harris = current_harris
            previous_trump = current_trump

        save_poll_data_to_csv(poll_data, './data/rcp_trump_harris_polls.csv')

        time.sleep(60)  # Wait for 1 minute before checking again


if __name__ == "__main__":
    main()








