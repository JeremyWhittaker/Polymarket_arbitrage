import csv
import json
import os
import time

from datetime import datetime, timedelta
from py_clob_client.client import ClobClient
from keys import api_key  # Import only the API key
from py_clob_client.clob_types import OpenOrderParams
from py_clob_client.exceptions import PolyApiException

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
# Function to load the condition_id to question mapping
def load_condition_id_question_mapping():
    if os.path.exists(mapping_file_path):
        with open(mapping_file_path, 'r') as f:
            return json.load(f)
    else:
        print("Mapping file not found. Please update the mapping first.")
        return {}


def create_state_condition_id_map():
    states = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
        "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
        "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
        "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"
    ]

    state_condition_map = {}
    for state in states:
        republican_keywords = ["Republican", "win", state, "Presidential Election"]
        democrat_keywords = ["Democrat", "win", state, "Presidential Election"]

        republican_match = search_questions(republican_keywords)
        democrat_match = search_questions(democrat_keywords)

        if republican_match and democrat_match:
            state_condition_map[state] = {
                "republican_id": republican_match[0][0],
                "democrat_id": democrat_match[0][0],
                "republican_question": republican_match[0][1],
                "democrat_question": democrat_match[0][1]
            }
        else:
            print(f"IDs not found for {state}.")

    return state_condition_map


def save_state_condition_map_to_file(state_condition_map, file_path="state_condition_map.json"):
    with open(file_path, 'w') as f:
        json.dump(state_condition_map, f, indent=2)
    print(f"State condition map saved to {file_path}")

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


def fetch_and_save_state_odds(state_condition_map, output_csv="state_odds.csv"):
    try:
        with open(output_csv, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            header = ["State", "Republican Odds", "Democrat Odds"]
            writer.writerow(header)

            for state, condition_ids in state_condition_map.items():
                republican_odds = get_market_price(condition_ids["republican_id"], "Yes")
                democrat_odds = get_market_price(condition_ids["democrat_id"], "Yes")

                writer.writerow([state, republican_odds, democrat_odds])
                print(f"Odds saved for {state}. Republican: {republican_odds}, Democrat: {democrat_odds}")

        print(f"All state odds have been saved to {output_csv}.")
    except IOError as e:
        print(f"Error writing to CSV: {e}")

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



if __name__ == "__main__":
    # Step 1: Create state-to-condition-ID map
    state_condition_map = create_state_condition_id_map()
    save_state_condition_map_to_file(state_condition_map)

    # Step 2: Fetch odds and save to CSV
    fetch_and_save_state_odds(state_condition_map)
