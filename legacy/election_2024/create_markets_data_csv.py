import csv
import json
from py_clob_client.client import ClobClient
from dotenv import load_dotenv
import os
from py_clob_client.clob_types import OpenOrderParams

# Load environment variables
load_dotenv("keys.env")
api_key = os.getenv('API_KEY')

# Replace with your actual host and chain ID
host = "https://clob.polymarket.com"
chain_id = 137  # Polygon Mainnet

# Initialize the client with only the host, key, and chain_id
client = ClobClient(
    host,
    chain_id=chain_id
)

# Initialize variables for pagination
markets_list = []
next_cursor = None

# Fetch all available markets using pagination
while True:
    try:
        # Print the cursor value for debugging
        print(f"Fetching markets with next_cursor: {next_cursor}")

        # Make the API call based on the cursor value
        if next_cursor is None:
            response = client.get_markets()
        else:
            response = client.get_markets(next_cursor=next_cursor)

        # Print the raw response for debugging
        print(f"API Response: {json.dumps(response, indent=2)}")

        # Check if the response is successful and contains data
        if 'data' not in response:
            print("No data found in response.")
            break

        markets_list.extend(response['data'])
        next_cursor = response.get("next_cursor")

        # Exit loop if there's no next_cursor indicating no more data to fetch
        if not next_cursor:
            break

    except Exception as e:
        # Print the exception details for debugging
        print(f"Exception occurred: {e}")
        print(f"Exception details: {e.__class__.__name__}")
        print(f"Error message: {e.args}")
        break

# Debugging step: Print out the raw data to understand its structure
print("Raw Market Data:")
print(json.dumps(markets_list, indent=2))

# Dynamically extract all keys from the markets to create the CSV columns
csv_columns = set()
for market in markets_list:
    csv_columns.update(market.keys())
    # Also include nested keys like tokens
    if 'tokens' in market:
        csv_columns.update({f"token_{key}" for token in market['tokens'] for key in token.keys()})

csv_columns = sorted(csv_columns)  # Sort columns alphabetically for consistency

# Writing to CSV
csv_file = "./data/markets_data.csv"
try:
    with open(csv_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
        writer.writeheader()
        for market in markets_list:
            row = {}
            for key in csv_columns:
                # Handling nested 'tokens' structure
                if key.startswith("token_"):
                    token_key = key[len("token_"):]
                    row[key] = ', '.join([str(token.get(token_key, 'N/A')) for token in market.get('tokens', [])])
                else:
                    row[key] = market.get(key, 'N/A')
            writer.writerow(row)
    print(f"Data has been written to {csv_file} successfully.")
except IOError as e:
    print(f"Error writing to CSV: {e}")