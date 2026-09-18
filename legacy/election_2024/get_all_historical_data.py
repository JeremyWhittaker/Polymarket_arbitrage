import os
import requests
import pandas as pd
import time  # To add delays if necessary
from dotenv import load_dotenv
import ast  # Import ast for safe evaluation of string to literal

# Load environment variables
load_dotenv()

# Ensure directories exist
os.makedirs('./data', exist_ok=True)
os.makedirs('./data/historical', exist_ok=True)

# Constants
API_KEY = os.getenv('API_KEY')
HOST = "https://clob.polymarket.com"
DATA_FILE = './data/markets_data.csv'  # CSV file instead of JSON

# Logging utility
def log_message(message, level="INFO"):
    print(f"{level}: {message}")

# Load market data from CSV file
def load_market_data_from_file(filepath):
    try:
        market_data = pd.read_csv(filepath)
        log_message(f"Loaded data for {len(market_data)} markets from {filepath}.")
        return market_data
    except Exception as e:
        log_message(f"Error loading market data from file: {e}", level="ERROR")
        return pd.DataFrame()

# Function to fetch time series data for a specific token_id
def fetch_timeseries_data(token_id, slug, outcome, fidelity=60, separator="_", save_to_csv=True):
    """
    Fetch timeseries data for a specific clob token from the Polymarket CLOB API and save it as Parquet and CSV files.
    Retrieves data at a minute-level fidelity.
    Skips requests for empty token_id or outcome.
    """
    if not token_id or not outcome:  # Check if token_id or outcome is empty
        log_message(f"Skipping fetch due to empty token_id or outcome for market slug: '{slug}'", level="WARNING")
        return []

    endpoint = f"{HOST}/prices-history"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    params = {
        "market": token_id,
        "interval": "max",  # Get the maximum interval available which could potentially be minute-level data
        "fidelity": fidelity  # Request for minute-level granularity
    }

    try:
        log_message(f"Fetching timeseries data for token_id: {token_id}, slug: '{slug}', outcome: '{outcome}'")
        response = requests.get(endpoint, headers=headers, params=params)
        log_message(f"Request URL: {response.url}")
        log_message(f"Response Status Code: {response.status_code}")

        response.raise_for_status()
        data = response.json()
        history = data.get("history", [])

        if history:
            log_message(f"Retrieved {len(history)} timeseries points for token_id: {token_id}.")
            df = pd.DataFrame(history)
            df['timestamp'] = pd.to_datetime(df['t'], unit='s', utc=True)
            df = df[['timestamp', 'p']].rename(columns={'p': 'price'})

            sanitized_slug = sanitize_filename(slug, separator)
            sanitized_outcome = sanitize_filename(outcome, separator)

            parquet_filename = f"./data/historical/{sanitized_slug}{separator}{sanitized_outcome}.parquet"
            csv_filename = f"./data/historical/{sanitized_slug}{separator}{sanitized_outcome}.csv"

            df.to_parquet(parquet_filename, index=False)
            log_message(f"Data saved to {parquet_filename} (Parquet format)")

            if save_to_csv:
                df.to_csv(csv_filename, index=False)
                log_message(f"Data saved to {csv_filename} (CSV format)")

            return history
        else:
            log_message(f"No data returned for token_id: {token_id}.")

    except requests.exceptions.HTTPError as http_err:
        log_message(f"HTTP error occurred for token_id {token_id}: {http_err}", level="ERROR")
    except Exception as err:
        log_message(f"An error occurred for token_id {token_id}: {err}", level="ERROR")

    return []


# Utility function to sanitize filenames
def sanitize_filename(filename, separator="_"):
    keep_characters = (' ', '.', '_', '-')
    sanitized = "".join(c for c in filename if c.isalnum() or c in keep_characters).rstrip()
    return sanitized.replace(' ', separator)

# Process market data
def process_market_data():
    market_data = load_market_data_from_file(DATA_FILE)
    # Correctly use .str accessor to apply .upper()
    open_markets = market_data[market_data['closed'].astype(str).str.strip().str.upper() == "FALSE"]

    for _, market in open_markets.iterrows():
        slug = market['market_slug']
        if pd.notna(market['tokens']):
            try:
                tokens = ast.literal_eval(market['tokens'])  # Safely evaluate the string to a list
                for token in tokens:
                    token_id = token['token_id']
                    outcome = token['outcome']
                    fetch_timeseries_data(token_id, slug, outcome, fidelity=60)
            except ValueError as e:
                log_message(f"Failed to parse tokens for market {slug}: {e}", level="ERROR")

if __name__ == "__main__":
    process_market_data()
