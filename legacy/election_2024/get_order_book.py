import os
import json
import logging
import pandas as pd
from py_clob_client.client import ClobClient
from strategies import trades  # Import the trades list
from dotenv import load_dotenv


# Access the environment variables
api_key = os.getenv('API_KEY')

# Initialize the ClobClient
host = "https://clob.polymarket.com"
chain_id = 137  # Polygon Mainnet
client = ClobClient(host, key=api_key, chain_id=chain_id)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def load_market_lookup():
    """Load the market lookup JSON to map slugs to token IDs."""
    with open('./data/market_lookup.json', 'r') as f:
        return json.load(f)

def fetch_and_save_order_book(token_id, market_id, slug, outcome):
    """
    Fetch the live order book for a given token ID and save it to a CSV file.

    Args:
        token_id (str): The token ID of the market.
        market_id (str): The market ID associated with the token.
        slug (str): The slug name of the market.
        outcome (str): The outcome ('Yes' or 'No') for the market.
    """
    try:
        order_book = client.get_order_book(token_id)
        if not hasattr(order_book, 'bids') or not hasattr(order_book, 'asks'):
            logging.error(f"Order book structure is not as expected for token_id: {token_id}")
            return

        book_data = []
        for side, orders in [('asks', order_book.asks), ('bids', order_book.bids)]:
            for order in orders:
                book_data.append({
                    'market_id': market_id,
                    'asset_id': order_book.asset_id,
                    'price': float(order.price),
                    'size': float(order.size),
                    'side': 'ask' if side == 'asks' else 'bid'
                })

        df = pd.DataFrame(book_data)
        if not df.empty:
            output_dir = "./data/book_data"
            os.makedirs(output_dir, exist_ok=True)
            file_name = f"{slug}_{outcome}.csv"  # Use the slug and outcome for the file name
            output_path = os.path.join(output_dir, file_name)
            df.to_csv(output_path, index=False)
            logging.info(f"Book data for {slug} ({outcome}) saved to {output_path}")
        else:
            logging.warning(f"No data found for token_id: {token_id}")

    except Exception as e:
        logging.error(f"Failed to fetch or save order book for token_id: {token_id}, error: {e}")


def update_books_for_trades():
    """Update order book data for the token IDs mentioned in the trades list."""
    market_lookup = load_market_lookup()
    slug_to_market = {market_data['market_slug']: market_data for market_data in market_lookup.values()}

    for trade in trades:
        positions = trade.get('positions', []) + trade.get('side_a_trades', []) + trade.get('side_b_trades', [])
        for slug, outcome in positions:
            market_data = slug_to_market.get(slug)
            if not market_data:
                logging.warning(f"Market slug {slug} not found in market lookup.")
                continue

            outcome_lower = outcome.lower()
            token_entry = next((token for token in market_data['tokens'] if token['outcome'].lower() == outcome_lower), None)

            if token_entry:
                token_id = token_entry['token_id']
                market_id = market_data.get('market_slug')
                logging.info(f"Fetching order book for market_id: {market_id}, token_id: {token_id}")
                fetch_and_save_order_book(token_id, market_id, slug, outcome)
            else:
                logging.warning(f"Token ID not found for slug: {slug} and outcome: {outcome}")



if __name__ == "__main__":
    update_books_for_trades()
