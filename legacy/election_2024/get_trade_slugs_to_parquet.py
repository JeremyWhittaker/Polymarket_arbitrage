import os
import requests
import pandas as pd
import json
from dotenv import load_dotenv
import argparse  # Added for argument parsing

# Load environment variables from a .env file if needed
load_dotenv()

# Access the environment variables
api_key = os.getenv('API_KEY')

# Ensure the data and historical directories exist
os.makedirs('./data', exist_ok=True)
os.makedirs('./data/historical', exist_ok=True)  # Create the historical subfolder

host = "https://clob.polymarket.com"

def fetch_timeseries_data(clob_token_id, slug, outcome, fidelity=60, separator="_", save_to_csv=True):
    """
    Fetches timeseries data for a specific clob token from the Polymarket CLOB API and saves it as a Parquet and optional CSV file.
    """
    endpoint = f"{host}/prices-history"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    params = {
        "market": clob_token_id,
        "interval": "max",
        "fidelity": fidelity
    }

    try:
        print(f"Preparing to fetch timeseries data for clobTokenId: {clob_token_id}, slug: '{slug}', outcome: '{outcome}'")

        response = requests.get(endpoint, headers=headers, params=params)
        print(f"Request URL for clobTokenId {clob_token_id}: {response.url}")
        response.raise_for_status()
        data = response.json()
        history = data.get("history", [])

        if history:
            print(f"Retrieved {len(history)} timeseries points for clobTokenId {clob_token_id}.")

            # Convert to DataFrame
            df = pd.DataFrame(history)
            df['timestamp'] = pd.to_datetime(df['t'], unit='s', utc=True)
            df = df[['timestamp', 'p']]  # Keep only relevant columns
            df.rename(columns={'p': 'price'}, inplace=True)

            # Sanitize file names
            sanitized_slug = sanitize_filename(slug, separator)
            sanitized_outcome = sanitize_filename(outcome, separator)

            print(f"Sanitized filename: Original: '{slug}', Sanitized: '{sanitized_slug}'")
            print(f"Sanitized outcome: Original: '{outcome}', Sanitized: '{sanitized_outcome}'")

            # Save to Parquet in the historical subfolder (default)
            parquet_filename = f"./data/historical/{sanitized_slug}{separator}{sanitized_outcome}.parquet"
            df.to_parquet(parquet_filename, index=False)
            print(f"Data saved to {parquet_filename} (Parquet format)")

            # Save to CSV if the flag is set to True
            if save_to_csv:
                csv_filename = f"./data/historical/{sanitized_slug}{separator}{sanitized_outcome}.csv"
                df.to_csv(csv_filename, index=False)
                print(f"Data saved to {csv_filename} (CSV format)")

        else:
            print(f"No timeseries data returned for clobTokenId {clob_token_id}.")
        return history

    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
    except Exception as err:
        print(f"An error occurred: {err}")

    return []

def sanitize_filename(filename, separator="_"):
    """
    Sanitizes a string to be used as a safe filename and replaces spaces with the specified separator.
    """
    keep_characters = (' ', '.', '_', '-')
    sanitized = "".join(c for c in filename if c.isalnum() or c in keep_characters).rstrip()
    return sanitized.replace(' ', separator)

if __name__ == "__main__":
    # Use argparse to accept command-line arguments
    parser = argparse.ArgumentParser(description='Fetch and save timeseries data for Polymarket CLOB token.')
    parser.add_argument('token_id', type=str, help='The CLOB token ID')
    parser.add_argument('market_slug', type=str, help='The market slug')
    parser.add_argument('outcome', type=str, help='The outcome (Yes or No)')

    args = parser.parse_args()

    # Fetch the timeseries data with provided arguments
    fetch_timeseries_data(args.token_id, args.market_slug, args.outcome, fidelity=1, save_to_csv=True)
