import os
import sys
import requests
import logging
import pandas as pd
import json
import time
from dotenv import load_dotenv
from get_polygon_data import process_wallet_data
from get_user_profile import get_user_info
from tqdm import tqdm
from strategies import trades

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Load environment variables
load_dotenv("keys.env")

# JSON file path for latest blockchain prices
LATEST_PRICES_FILE = './data/latest_blockchain_prices.json'

# EXCHANGES
CTF_EXCHANGE = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
NEG_RISK_CTF_EXCHANGE = '0x4d97dcd97ec945f40cf65f87097ace5ea0476045'

# SPENDERS FOR EXCHANGES
NEG_RISK_CTF_EXCHANGE_SPENDER = '0xC5d563A36AE78145C45a50134d48A1215220f80a'
NEG_RISK_ADAPTER = '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296'
CTF_EXCHANGE_SPENDER = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'

def get_slug_and_outcome_by_token_id(token_id, market_lookup):
    """
    Find the market_slug and outcome based on the token_id.

    Args:
    - token_id (str): The token ID to look up.
    - market_lookup (dict): The dictionary containing market data.

    Returns:
    - (market_slug, outcome) or (None, None): The slug and outcome if found, otherwise None.
    """
    # Log the token ID we're looking for
    logger.debug(f"Looking for token_id: {token_id} in market lookup")

    for market in market_lookup.values():
        for token in market['tokens']:
            logger.debug(f"Checking token_id: {token['token_id']}")
            if token['token_id'] == str(token_id):  # Ensure both are strings
                return market['market_slug'], token['outcome']

    return None, None


def load_market_lookup(json_path):
    """Load market lookup data from a JSON file."""
    with open(json_path, 'r') as json_file:
        return json.load(json_file)


def find_token_id(market_slug, outcome, market_lookup):
    """Find the token_id based on market_slug and outcome."""
    for market in market_lookup.values():
        if market['market_slug'] == market_slug:
            for token in market['tokens']:
                if token['outcome'].lower() == outcome.lower():
                    return token['token_id']
    return None


def fetch_data(url):
    """Fetch data from a given URL and return the JSON response."""
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Failed to fetch data from URL: {url}, Status Code: {response.status_code}")
        return None


def replace_hex_with_names(value):
    """Replace hex values with human-readable names using mapping."""
    hex_to_name_mapping = {
        CTF_EXCHANGE.lower(): 'CTF_EXCHANGE',
        NEG_RISK_CTF_EXCHANGE.lower(): 'NEG_RISK_CTF_EXCHANGE',
        NEG_RISK_CTF_EXCHANGE_SPENDER.lower(): 'NEG_RISK_CTF_EXCHANGE_SPENDER',
        NEG_RISK_ADAPTER.lower(): 'NEG_RISK_ADAPTER',
        CTF_EXCHANGE_SPENDER.lower(): 'CTF_EXCHANGE_SPENDER'
    }
    return hex_to_name_mapping.get(value.lower(), value)


def update_latest_prices_file(market_slug, outcome, latest_price, timestamp=None, shares=None, volume=None):
    """
    Update the latest_blockchain_prices.json file with the latest price, timestamp, shares, and volume.
    Only writes if the new timestamp is more recent than the existing one.
    """
    json_file = './data/latest_blockchain_prices.json'

    # Load existing data from JSON
    if os.path.exists(json_file):
        with open(json_file, 'r') as f:
            data = json.load(f)
    else:
        data = {}

    # Ensure market_slug is in the data
    if market_slug not in data:
        data[market_slug] = {}

    # Check if the outcome exists and has a timestamp, and compare it to the incoming one
    existing_data = data[market_slug].get(outcome, {})
    existing_timestamp = existing_data.get('timestamp')

    if existing_timestamp:
        try:
            # Convert both timestamps to integers (assuming they are in Unix timestamp format)
            existing_timestamp = int(existing_timestamp)
            new_timestamp = int(timestamp)

            # Skip update if the existing timestamp is newer or equal
            if existing_timestamp >= new_timestamp:
                logger.info(f"Skipping update for {market_slug} - {outcome}: existing timestamp {existing_timestamp} is newer or equal to the incoming timestamp {new_timestamp}.")
                return
        except ValueError:
            logger.error(f"Error converting timestamps to integers for comparison. Existing: {existing_timestamp}, New: {timestamp}")
            return

    # Update with new information if the new timestamp is newer
    data[market_slug][outcome] = {
        "latest_price": latest_price,
        "timestamp": timestamp,
        "shares": shares,
        "volume": volume
    }

    # Write back to JSON file
    with open(json_file, 'w') as f:
        json.dump(data, f, indent=4)

    logger.info(f"Updated {json_file} with latest price and additional details for {market_slug} - {outcome}")

def filter_latest_timestamp_rows(df):
    """
    Filter the DataFrame to keep only the rows with the latest timeStamp.
    """
    # Convert 'timeStamp' to numeric first, then to datetime format (avoiding FutureWarning)
    df.loc[:, 'timeStamp'] = pd.to_numeric(df['timeStamp'], errors='coerce')
    df.loc[:, 'timeStamp'] = pd.to_datetime(df['timeStamp'], unit='s')

    # Find the latest timestamp
    latest_timestamp = df['timeStamp'].max()

    # Filter the rows to keep only those with the latest timestamp
    df_filtered_latest = df[df['timeStamp'] == latest_timestamp]

    logger.info(f"Filtered to {len(df_filtered_latest)} rows with the latest timestamp: {latest_timestamp}")

    return df_filtered_latest


def get_latest_transactions_for_markets(api_key, market_slug_outcome_list, market_lookup,
                                        csv_output_dir='./data/polymarket_trades/', max_retries=5, backoff_factor=2):
    """
    Fetch the latest transactions for a list of markets (slugs and outcomes) and process them.
    This avoids multiple API calls by fetching the last 10,000 transactions in one go.
    """
    offset = 10000  # Fetch the maximum allowed records in one request
    retry_attempts = 0

    # Ensure the output directory exists
    os.makedirs(csv_output_dir, exist_ok=True)

    # Replace hex with a more meaningful name for file naming
    contract_name = "NEG_RISK_CTF_EXCHANGE"

    # API URL to get last 10,000 transactions
    url = f"https://api.polygonscan.com/api?module=account&action=token1155tx&contractaddress={NEG_RISK_CTF_EXCHANGE}&page=1&offset={offset}&startblock=0&endblock=99999999&sort=desc&apikey={api_key}"

    logger.info(f"Fetching last {offset} transactions for the contract.")
    logger.info(f"Request URL: {url}")

    try:
        # Fetch data from Polygonscan
        data = fetch_data(url)

        if data and data['status'] == '1':
            df = pd.DataFrame(data['result'])

            for slug, outcome in market_slug_outcome_list:
                logger.info(f"Processing transactions for market: {slug}, outcome: {outcome}")

                # Find token ID for this market slug and outcome
                token_id = find_token_id(slug, outcome, market_lookup)

                if not token_id:
                    logger.warning(f"Could not find token ID for market slug: '{slug}' and outcome: '{outcome}'")
                    continue

                # Filter by tokenID
                df_filtered = df[df['tokenID'] == str(token_id)]
                if df_filtered.empty:
                    logger.warning(f"No transactions found for tokenID: {token_id} for market: {slug}")
                    continue

                # Filter for the latest timestamp rows
                df_filtered = filter_latest_timestamp_rows(df_filtered)

                # Replace hex values with human-readable names
                df_filtered.loc[:, 'from'] = df_filtered['from'].apply(replace_hex_with_names)
                df_filtered.loc[:, 'to'] = df_filtered['to'].apply(replace_hex_with_names)
                df_filtered.loc[:, 'contractAddress'] = df_filtered['contractAddress'].apply(replace_hex_with_names)

                logger.info(f"Hex values replaced with corresponding names for market: {slug}, outcome: {outcome}")

                # Save filtered data to CSV using the exchange_slug_outcome format
                contract_name = "NEG_RISK_CTF_EXCHANGE"
                csv_filename = f"{contract_name}_{slug}_{outcome}.csv"
                csv_filepath = os.path.join(csv_output_dir, csv_filename)
                df_filtered.to_csv(csv_filepath, index=False)
                logger.info(f"Filtered transaction data saved to {csv_filepath}")

                # Define a list of wallets to exclude from processing
                exclude_wallets = [
                    'NEG_RISK_ADAPTER',
                    'NEG_RISK_CTF_EXCHANGE_SPENDER',
                    'CTF_EXCHANGE_SPENDER',
                    '0x0000000000000000000000000000000000000000'
                ]

                # Filter out excluded wallets and get unique wallet IDs
                wallets_to_check = df_filtered['from'][~df_filtered['from'].isin(exclude_wallets)].unique().tolist()
                wallets_to_check += df_filtered['to'][~df_filtered['to'].isin(exclude_wallets)].unique().tolist()

                # Ensure uniqueness of wallets
                wallets_to_check = list(set(wallets_to_check))

                for wallet in tqdm(wallets_to_check, desc=f"Processing wallets for {slug}", unit="wallet"):
                    logger.info(f"Fetching user info for wallet: {wallet}")
                    user_info = get_user_info(wallet)
                    if user_info:
                        username = user_info['username']
                        logger.info(f"Username for wallet {wallet}: {username}")

                        # Process wallet data for each wallet found
                        process_wallet_data([wallet], api_key, plot=False, latest_price_mode=True)

                        # After CSVs are generated, process the CSV for this user based on their username
                        process_wallet_csv_files(token_id, csv_directory='./data/user_trades/', username=username,
                                                 market_slug=slug, outcome=outcome, market_lookup=market_lookup)
                    else:
                        logger.warning(f"Could not find user information for wallet: {wallet}")
        else:
            logger.error(f"API response error: {data['message'] if data else 'Unknown error'}")

    except Exception as e:
        logger.error(f"Exception occurred while fetching data: {e}")



def process_wallet_csv_files(contract_token_id, csv_directory='./data/polymarket_trades/', username=None,
                             market_slug=None, outcome=None, market_lookup=None):
    """
    Process the CSV file for the specific user who made the latest transaction,
    filter by the contract's tokenID, and get the latest transaction price and other details.
    """
    latest_price = None

    logger.info(f"Processing CSV for user: {username}, market: {market_slug}, outcome: {outcome}")

    # Check if market_lookup is None or empty
    if market_lookup is None or not market_lookup:
        logger.error("Error: market_lookup is None or empty. Ensure the market lookup file is loaded correctly.")
        return latest_price

    if username and market_slug and outcome:
        # Build the expected CSV filename using the new format
        csv_file = f"{username}_enriched_transactions.csv"
        file_path = os.path.join(csv_directory, csv_file)

        logger.debug(f"Looking for CSV file at path: {file_path}")

        try:
            if os.path.exists(file_path):
                logger.info(f"CSV file found: {file_path}")

                # Load the CSV into a pandas DataFrame
                df = pd.read_csv(file_path)
                logger.debug(f"CSV loaded successfully. Dataframe shape: {df.shape}")

                # Filter by the tokenID
                df_filtered = df[df['tokenID'] == contract_token_id]
                logger.debug(f"Filtering by tokenID {contract_token_id}. Filtered DataFrame shape: {df_filtered.shape}")

                if not df_filtered.empty:
                    # Sort by 'timeStamp_erc1155', which is already in datetime format
                    logger.debug(f"Sorting filtered DataFrame by 'timeStamp_erc1155'")
                    df_filtered = df_filtered.sort_values(by='timeStamp_erc1155', ascending=False)

                    # Get the latest row and extract the necessary transaction details
                    latest_transaction = df_filtered.iloc[0]
                    latest_price = latest_transaction['price_paid_per_token']
                    timestamp = latest_transaction['timeStamp_erc1155']
                    shares = latest_transaction['shares']
                    total_purchase_value = latest_transaction['total_purchase_value']

                    logger.info(f"Processed latest transaction with price: {latest_price}, shares: {shares}, volume: {total_purchase_value}")

                    # Update latest_blockchain_prices.json with additional transaction details
                    update_latest_prices_file(
                        market_slug,
                        outcome,
                        latest_price,
                        timestamp=timestamp,
                        shares=shares,
                        volume=total_purchase_value
                    )

                    # Lookup the slug and outcome from the market lookup
                    market_slug, outcome = get_slug_and_outcome_by_token_id(str(contract_token_id), market_lookup)

                    # Log the slug and outcome
                    logger.debug(f"Market Slug: {market_slug}, Outcome: {outcome}")

                    # Update the log to show slug and outcome instead of token ID
                    if market_slug and outcome:
                        logger.info(
                            f"Processed {csv_file}: Latest price {latest_price} for market '{market_slug}' and outcome '{outcome}'"
                        )
                    else:
                        logger.info(
                            f"Processed {csv_file}: Latest price {latest_price} for tokenID {contract_token_id}"
                        )
                else:
                    logger.info(f"No matching tokenID {contract_token_id} found in {csv_file}")
            else:
                logger.error(f"CSV file {csv_file} does not exist in directory: {csv_directory}")

        except Exception as e:
            logger.error(f"Error processing {csv_file}: {e}")

    else:
        logger.warning(f"Missing username, market_slug, or outcome. Skipping processing.")

    return latest_price

def extract_trades():
    """
    Extracts all the slugs and outcomes from the trades in the strategies.py file.
    Returns a list of tuples containing (slug, outcome).
    """
    slug_outcome_list = []

    for trade in trades:
        # 'all_no' method - iterate over the 'positions'
        if trade['method'] == 'all_no':
            slug_outcome_list.extend(trade['positions'])

        # 'balanced' method - iterate over both side_a_trades and side_b_trades
        elif trade['method'] == 'balanced':
            slug_outcome_list.extend(trade.get('side_a_trades', []))
            slug_outcome_list.extend(trade.get('side_b_trades', []))

    return slug_outcome_list

def main():
    # Load the API key from the environment variables
    api_key = os.getenv('POLYGONSCAN_API_KEY')

    if not api_key:
        logging.error("API key not found. Please check your environment variables.")
        return

    # Load market lookup from JSON
    try:
        market_lookup = load_market_lookup('./data/market_lookup.json')
        if not market_lookup:
            logging.error("Failed to load market_lookup. The file might be empty or incorrectly formatted.")
            return
    except Exception as e:
        logging.error(f"Error loading market_lookup.json: {e}")
        return

    # Check if the script is called with command-line arguments or run directly
    if len(sys.argv) == 2:
        # If command-line arguments are provided, parse them
        try:
            market_slug_outcome_list = json.loads(sys.argv[1])
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON input: {e}")
            sys.exit(1)
    else:
        # If no arguments are provided, extract markets from strategies.py
        logging.info("No command-line arguments provided. Extracting market slugs and outcomes from strategies.")
        market_slug_outcome_list = extract_trades()
        logging.info(f"Using extracted markets: {market_slug_outcome_list}")

    # Call the function to get the latest transactions for all markets
    get_latest_transactions_for_markets(api_key, market_slug_outcome_list, market_lookup)

def run_continuously():
    """
    Run the process continuously to update prices for all items from strategies.py.
    """
    while True:
        logging.info("Running continuous price fetching...")
        main()  # Call the main function for each iteration
        time.sleep(300)  # Wait for 5 minutes before fetching again

if __name__ == "__main__":
    # If command-line args are passed, run once. Otherwise, run continuously.
    if len(sys.argv) > 1:
        main()
    else:
        run_continuously()