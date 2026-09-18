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
import logging
from dotenv import load_dotenv
from get_leaderboard_wallet_ids import scrape_wallet_ids

# Load environment variables
load_dotenv('keys.env')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()


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
    # Use the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if extract_path is None:
        extract_path = os.path.join(script_dir, 'chromedriver')

    version_file_path = os.path.join(extract_path, 'version.txt')

    # Check if the correct version is already downloaded
    if os.path.exists(extract_path) and os.path.exists(version_file_path):
        with open(version_file_path, 'r') as version_file:
            installed_version = version_file.read().strip()
        if installed_version == version:
            chromedriver_path = os.path.join(extract_path, 'chromedriver-linux64', 'chromedriver')
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

    return chromedriver_path




def save_user_data_to_json(user_data, output_path):
    """Save the user data to a JSON file."""
    with open(output_path, 'w') as json_file:
        json.dump(user_data, json_file, indent=4)
    logger.info(f"User data saved to {output_path}")


def get_chromedriver_path():
    """Get the path to the ChromeDriver based on the current script location."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    chromedriver_path = os.path.join(script_dir, 'chromedriver', 'chromedriver-linux64', 'chromedriver')
    if not os.path.exists(chromedriver_path):
        raise FileNotFoundError(f"ChromeDriver not found at {chromedriver_path}")
    return chromedriver_path

def get_user_info(wallet_address):
    """Fetch user info from Polymarket using the wallet address."""
    url = f"https://polymarket.com/profile/{wallet_address}"

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")

    # Use the dynamic ChromeDriver path
    chromedriver_path = get_chromedriver_path()
    driver = webdriver.Chrome(service=Service(chromedriver_path), options=chrome_options)

    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1.c-ipOUDc")))
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract the username
        username = soup.select_one("h1.c-ipOUDc").text.strip()

        # Extract positions value
        positions_value_elements = soup.find_all('p', class_='c-dqzIym c-fcWvkb c-dqzIym-fxyRaa-color-normal c-dqzIym-cTvRMP-spacing-normal c-dqzIym-iIobgq-weight-medium')
        positions_value = positions_value_elements[0].text.strip() if len(positions_value_elements) > 0 else "N/A"

        # Extract profit/loss
        profit_loss = positions_value_elements[1].text.strip() if len(positions_value_elements) > 1 else "N/A"

        # Extract volume traded
        volume_traded = positions_value_elements[2].text.strip() if len(positions_value_elements) > 2 else "N/A"

        # Extract markets traded
        markets_traded = positions_value_elements[3].text.strip() if len(positions_value_elements) > 3 else "N/A"

        # Extract joined date
        joined_date_element = soup.find('p', class_='c-dqzIym c-dqzIym-fxyRaa-color-normal c-dqzIym-cTvRMP-spacing-normal c-dqzIym-jalaKP-weight-normal c-dqzIym-hzzdKO-size-md c-dqzIym-ibGjNZs-css')
        joined_date = joined_date_element.text.strip() if joined_date_element else "N/A"

        # Extract user description (if exists)
        profile_description_element = soup.find('p', class_='c-dqzIym c-dqzIym-fxyRaa-color-normal c-dqzIym-cTvRMP-spacing-normal c-dqzIym-jalaKP-weight-normal c-dqzIym-idxllRe-css')
        profile_description = profile_description_element.text.strip() if profile_description_element else "No description provided"

        return {
            "username": username,
            "positions_value": positions_value,
            "profit_loss": profit_loss,
            "volume_traded": volume_traded,
            "markets_traded": markets_traded,
            "joined_date": joined_date,
            "wallet_address": wallet_address,  # Add wallet_address to the dictionary
            "profile_description": profile_description,  # Add profile description to the dictionary
            "wallet_address": wallet_id  # Include the wallet_address

        }
    finally:
        driver.quit()



def main(wallet_id):
    """
    Scrapes and returns user data for the given wallet_id.
    Instead of saving to file, outputs the result as JSON via stdout.
    """
    try:
        # Scrape user data
        logger.info(f"Scraping data for wallet: {wallet_id}")
        user_data = get_user_info(wallet_id)

        # Print user data as JSON to stdout
        print(json.dumps(user_data, indent=4))

    except Exception as e:
        logger.error(f"Failed to scrape data for wallet {wallet_id}: {e}")
        print(json.dumps({'error': str(e)}))

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        logger.error("No wallet ID provided.")
        sys.exit(1)

    wallet_id = sys.argv[1]  # Expect wallet_id as a command-line argument
    main(wallet_id)