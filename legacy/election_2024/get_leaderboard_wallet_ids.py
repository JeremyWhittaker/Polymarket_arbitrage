import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import json
import argparse

# Load environment variables
load_dotenv('keys.env')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()


def get_chromedriver_path():
    """Get the path to the ChromeDriver based on the current script location."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    chromedriver_path = os.path.join(script_dir, 'chromedriver', 'chromedriver-linux64', 'chromedriver')
    if not os.path.exists(chromedriver_path):
        raise FileNotFoundError(f"ChromeDriver not found at {chromedriver_path}")
    return chromedriver_path


def scrape_wallet_ids(leaderboard_type='volume', time_period='Day'):
    """Scrape wallet IDs from the leaderboard by leaderboard type (volume or profit) and time period (Day, Week, Month, All)."""
    url = "https://polymarket.com/leaderboard"  # Replace with the actual leaderboard URL

    # Initialize the WebDriver
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(service=Service(get_chromedriver_path()), options=chrome_options)
    driver.get(url)

    wallet_ids = set()  # Use a set to avoid duplicates

    try:
        # Wait until the leaderboard is loaded
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".c-dhzjXW"))
        )

        # Click on the appropriate tab for leaderboard type (Volume or Profit)
        logger.info(f"Clicking on {leaderboard_type.capitalize()} leaderboard tab.")

        if leaderboard_type == 'volume':
            leaderboard_tab_xpath = "//p[text()='Volume']"
        elif leaderboard_type == 'profit':
            leaderboard_tab_xpath = "//p[text()='Profit']"
        else:
            logger.error("Invalid leaderboard type provided.")
            driver.quit()
            return list(wallet_ids)

        leaderboard_tab_element = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, leaderboard_tab_xpath))
        )
        leaderboard_tab_element.click()
        time.sleep(2)  # Wait for the content to load after clicking

        # Parse the page content with BeautifulSoup
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract wallet IDs
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if href.startswith('/profile/'):
                wallet_id = href.split('/')[-1]
                wallet_ids.add(wallet_id)  # Add to the set to avoid duplicates

        logger.info(f"Extracted wallet IDs from {leaderboard_type.capitalize()} leaderboard.")

    except Exception as e:
        logger.error(f"An error occurred while processing {leaderboard_type.capitalize()} leaderboard: {e}")
    finally:
        driver.quit()

    return list(wallet_ids)


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Scrape the leaderboard for top volume or top profit users.")
    parser.add_argument('--top-volume', action='store_true', help="Scrape the top volume leaderboard")
    parser.add_argument('--top-profit', action='store_true', help="Scrape the top profit leaderboard")

    args = parser.parse_args()

    wallet_ids = []

    # Call the scrape_wallet_ids function based on the flags passed
    if args.top_volume:
        logger.info("Scraping top volume leaderboard.")
        wallet_ids.extend(scrape_wallet_ids(leaderboard_type='volume'))
    if args.top_profit:
        logger.info("Scraping top profit leaderboard.")
        wallet_ids.extend(scrape_wallet_ids(leaderboard_type='profit'))

    # Output wallet IDs as JSON
    print(json.dumps(wallet_ids))


if __name__ == "__main__":
    main()