import csv
import json
import os
import time

from datetime import datetime, timedelta
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OpenOrderParams
from py_clob_client.exceptions import PolyApiException


# Access the environment variables
api_key = os.getenv('API_KEY')

# Replace with your actual host and chain ID
host = "https://clob.polymarket.com"
chain_id = 137  # Polygon Mainnet
mapping_file_path = "old/condition_id_question_mapping.json"

# Initialize the client with only the host, key, and chain_id
client = ClobClient(
    host,
    key=api_key,
    chain_id=chain_id
)


def fetch_all_markets(client):
    markets_list = []
    next_cursor = None
    while True:
        try:
            print(f"Fetching markets with next_cursor: {next_cursor}")
            if next_cursor is None:
                response = client.get_markets()
            else:
                response = client.get_markets(next_cursor=next_cursor)

            print(f"API Response: {json.dumps(response, indent=2)}")
            if 'data' not in response:
                print("No data found in response.")
                break

            markets_list.extend(response['data'])
            next_cursor = response.get("next_cursor")
            if not next_cursor:
                break

        except Exception as e:
            print(f"Exception occurred: {e}")
            print(f"Exception details: {e.__class__.__name__}")
            print(f"Error message: {e.args}")
            break

    print("Raw Market Data:")
    print(json.dumps(markets_list, indent=2))

    return markets_list

def extract_specific_market_details(client, condition_id):
    try:
        market_data = client.get_market(condition_id=condition_id)
        if market_data:
            print("Market Data Found:")
            print(json.dumps(market_data, indent=2))
            return market_data
        else:
            print("Market data not found or invalid condition_id.")
    except Exception as e:
        print(f"Exception occurred: {e}")
        print(f"Exception details: {e.__class__.__name__}")
        print(f"Error message: {e.args}")

def write_markets_to_csv(markets_list, csv_file="./data/markets_data.csv"):
    csv_columns = set()
    for market in markets_list:
        csv_columns.update(market.keys())
        if 'tokens' in market:
            csv_columns.update({f"token_{key}" for token in market['tokens'] for key in token.keys()})

    csv_columns = sorted(csv_columns)

    try:
        with open(csv_file, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
            writer.writeheader()
            for market in markets_list:
                row = {}
                for key in csv_columns:
                    if key.startswith("token_"):
                        token_key = key[len("token_"):]
                        row[key] = ', '.join([str(token.get(token_key, 'N/A')) for token in market.get('tokens', [])])
                    else:
                        row[key] = market.get(key, 'N/A')
                writer.writerow(row)
        print(f"Data has been written to {csv_file} successfully.")
    except IOError as e:
        print(f"Error writing to CSV: {e}")


def fetch_market_prices(client, condition_id):
    try:
        market_data = client.get_market(condition_id=condition_id)
        if market_data:
            # Extract Yes and No prices
            yes_price = None
            no_price = None

            for token in market_data.get('tokens', []):
                if token['outcome'].lower() == 'yes':
                    yes_price = token['price']
                elif token['outcome'].lower() == 'no':
                    no_price = token['price']

            if yes_price is not None and no_price is not None:
                print(f"Market: {market_data['question']}")
                print(f"Yes Price: {yes_price}")
                print(f"No Price: {no_price}")
            else:
                print("Yes or No price not found in the market data.")
        else:
            print("Market data not found or invalid condition_id.")
    except Exception as e:
        print(f"Exception occurred: {e}")
        print(f"Exception details: {e.__class__.__name__}")
        print(f"Error message: {e.args}")

# Assuming the ClobClient and the necessary initialization is done above this point
# Function to fetch market data based on condition_id and outcome
def get_market_price(condition_id, outcome):
    try:
        market_data = client.get_market(condition_id=condition_id)
        if market_data and 'tokens' in market_data:
            for token in market_data['tokens']:
                if token['outcome'].lower() == outcome.lower():
                    return token['price']
        print(f"Price not found for condition_id: {condition_id} with outcome: {outcome}")
    except Exception as e:
        print(f"Exception occurred while fetching market data: {e}")
    return None

# Function to calculate arbitrage percentage between two lists of trades
# Assuming the ClobClient and the necessary initialization is done above this point
# Function to fetch market data based on condition_id and outcome
def get_market_data(condition_id):
    try:
        market_data = client.get_market(condition_id=condition_id)
        if market_data:
            return market_data
    except Exception as e:
        print(f"Exception occurred while fetching market data: {e}")
    return None


# Function to fetch all market data and create a mapping
def create_condition_id_question_mapping():
    markets_list = fetch_all_markets(client)
    if not markets_list:
        print("No markets data available to create the mapping.")
        return

    # Create the dictionary mapping
    condition_id_question_map = {market['condition_id']: market['question'] for market in markets_list}

    # Save the mapping to a file
    with open(mapping_file_path, 'w') as f:
        json.dump(condition_id_question_map, f, indent=2)
    print(f"Condition ID to Question mapping saved to {mapping_file_path}")

# Function to check if the mapping file needs to be updated
def update_mapping_if_needed():
    if os.path.exists(mapping_file_path):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(mapping_file_path))
        if datetime.now() - file_mod_time > timedelta(days=1):
            print("Updating the mapping file.")
            create_condition_id_question_mapping()
        else:
            print("Mapping file is up-to-date.")
    else:
        print("Mapping file does not exist, creating new one.")
        create_condition_id_question_mapping()

# Function to load the condition_id to question mapping
def load_condition_id_question_mapping():
    if os.path.exists(mapping_file_path):
        with open(mapping_file_path, 'r') as f:
            return json.load(f)
    else:
        print("Mapping file not found. Please update the mapping first.")
        return {}

# Function to search for keywords in questions
def search_questions(keywords):
    update_mapping_if_needed()
    condition_id_question_map = load_condition_id_question_mapping()
    if not condition_id_question_map:
        print("No mapping data available.")
        return []

    # Ensure all keywords must be found in the question
    def all_keywords_in_question(question, keywords):
        return all(keyword.lower() in question.lower() for keyword in keywords)

    matched_items = [
        (condition_id, question)
        for condition_id, question in condition_id_question_map.items()
        if all_keywords_in_question(question, keywords)
    ]

    # Print matched condition IDs along with their corresponding questions
    print(f"Matched Condition IDs and Questions for keywords '{', '.join(keywords)}':")
    for condition_id, question in matched_items:
        print(f"Condition ID: {condition_id}")
        print(f"Question: {question}\n")

    return matched_items


def calculate_multiple_arbitrage_opportunities(arb_opportunities):
    results = []

    for opportunity in arb_opportunities:
        strategy_name, side_a_ids, side_b_ids, side_a_outcome, side_b_outcome = opportunity

        side_a_info = []
        side_b_info = []

        side_a_cost = 0
        side_b_cost = 0

        for condition_id in side_a_ids:
            market_data = get_market_data(condition_id)
            if market_data:
                for token in market_data['tokens']:
                    if token['outcome'].lower() == side_a_outcome.lower():
                        price = token['price']
                        side_a_cost += price
                        side_a_info.append((market_data['question'], side_a_outcome, price))

        for condition_id in side_b_ids:
            market_data = get_market_data(condition_id)
            if market_data:
                for token in market_data['tokens']:
                    if token['outcome'].lower() == side_b_outcome.lower():
                        price = token['price']
                        side_b_cost += price
                        side_b_info.append((market_data['question'], side_b_outcome, price))

        total_cost = side_a_cost + side_b_cost
        arb_percentage = (1 - total_cost) * 100

        # Log the detailed information with side costs
        print(
            f"Arbitrage Opportunity Found! {strategy_name} - Arbitrage Percentage: {arb_percentage}% (Side A: {side_a_cost}, Side B: {side_b_cost})")

        results.append((strategy_name, arb_percentage))

    return results


def update_csv_file_every_minute(csv_file, arb_opportunities):
    while True:
        # Calculate arbitrage opportunities
        arb_results = calculate_multiple_arbitrage_opportunities(arb_opportunities)

        # Prepare the data to write to the CSV
        timestamp = datetime.now().strftime("%m/%d/%Y %H:%M")
        row_data = [timestamp]

        for result in arb_results:
            strategy_name, arb_percentage = result
            row_data.append(arb_percentage)

        # Write the data to the CSV
        try:
            file_exists = os.path.isfile(csv_file)
            with open(csv_file, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)

                # If file does not exist, write the header
                if not file_exists:
                    header = ["Timestamp"] + [result[0] for result in arb_results]
                    writer.writerow(header)

                writer.writerow(row_data)
            print(f"Data appended to {csv_file} at {timestamp}")
        except IOError as e:
            print(f"Error writing to CSV: {e}")

        # Sleep for 1 minute
        time.sleep(60)

if __name__ == "__main__":
    # Fetch all markets and write to CSV
    markets_list = fetch_all_markets(client)
    write_markets_to_csv(markets_list)
