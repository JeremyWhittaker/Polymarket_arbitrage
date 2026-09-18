import pandas as pd
import json
import ast

def create_market_lookup(csv_file, output_json_file):
    """Create a JSON lookup from a CSV file for condition_id to description, market_slug, and tokens."""

    # Read the CSV file
    df = pd.read_csv(csv_file)

    # Drop duplicate condition_ids, keeping the first occurrence
    lookup_df = df[['condition_id', 'description', 'market_slug', 'tokens']].drop_duplicates(subset='condition_id')

    # Initialize an empty dictionary to store the lookup
    lookup_dict = {}

    for index, row in lookup_df.iterrows():
        # Parse the tokens column (it's assumed to be a string representation of a list of dictionaries)
        tokens_list = ast.literal_eval(row['tokens'])

        # Extract token_id and outcome from each token
        tokens_info = [{"token_id": token["token_id"], "outcome": token["outcome"]} for token in tokens_list]

        # Add the information to the lookup dictionary
        lookup_dict[row['condition_id']] = {
            "description": row['description'],
            "market_slug": row['market_slug'],
            "tokens": tokens_info
        }

    # Save the dictionary as a JSON file
    with open(output_json_file, 'w') as json_file:
        json.dump(lookup_dict, json_file, indent=4)

    print(f"Lookup JSON file created: {output_json_file}")

# Usage
csv_file = './data/markets_data.csv'  # Replace with your actual file path
output_json_file = './data/market_lookup.json'
create_market_lookup(csv_file, output_json_file)



def query_description_by_keyword(lookup_json, keyword):
    with open(lookup_json, 'r') as json_file:
        lookup_dict = json.load(json_file)

    results = {cond_id: info for cond_id, info in lookup_dict.items() if keyword.lower() in info['description'].lower()}
    return results


def get_market_slug_by_condition_id(lookup_json, condition_id):
    with open(lookup_json, 'r') as json_file:
        lookup_dict = json.load(json_file)

    return lookup_dict.get(condition_id, {}).get('market_slug')



# Usage
csv_file = './data/markets_data.csv'
output_json_file = './data/market_lookup.json'
create_market_lookup(csv_file, output_json_file)


# Usage examples
# print(query_description_by_keyword('market_lookup.json', 'Trump'))
print(get_market_slug_by_condition_id('./data/market_lookup.json',
                                      '0x84dfb8b5cac6356d4ac7bb1da55bb167d0ef65d06afc2546389630098cc467e9'))
