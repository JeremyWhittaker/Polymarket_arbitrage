import os
import time
import logging
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

# Load environment variables from the .env file
load_dotenv()

# Access the environment variables
API_KEY = os.getenv('API_KEY')
host = "https://clob.polymarket.com"
chain_id = 137  # Polygon Mainnet

print(f"Using API key: {API_KEY}")

# Initialize the ClobClient
client = ClobClient(host, chain_id=chain_id)

# Dictionary to cache live prices
live_price_cache = {}
CACHE_DURATION = 60  # Cache live prices for 1 minute


def get_live_price(token_id):
    """
    Fetch the live price for a given token ID.

    Args:
        token_id (str): The token ID for which the live price is being requested.

    Returns:
        float: The live price for the given token ID.
    """
    cache_key = f"{token_id}"
    current_time = time.time()

    # Check if the price is in the cache and still valid
    if cache_key in live_price_cache:
        cached_price, timestamp = live_price_cache[cache_key]
        if current_time - timestamp < CACHE_DURATION:
            logger.info(f"Returning cached price for {cache_key}: {cached_price}")
            return cached_price
        else:
            logger.info(f"Cache expired for {cache_key}. Fetching a new price.")

    # Fetch new price from the API
    try:
        response = client.get_last_trade_price(token_id=token_id)
        price = response.get('price')

        # Cache the price with the current timestamp
        live_price_cache[cache_key] = (price, current_time)
        logger.info(f"Fetched live price for {cache_key}: {price}")
        return price
    except Exception as e:
        logger.error(f"Failed to fetch live price for token {token_id}: {str(e)}")
        return None


# If this script is executed directly, it can take command-line arguments to test the live price retrieval
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python get_live_price.py <token_id>")
        sys.exit(1)

    token_id = sys.argv[1]

    live_price = get_live_price(token_id)
    if live_price is not None:
        print(f"Live price for token {token_id}: {live_price}")
    else:
        print(f"Could not fetch the live price for token {token_id}.")
