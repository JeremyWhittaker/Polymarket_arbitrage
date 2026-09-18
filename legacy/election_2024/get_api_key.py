import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from dotenv import load_dotenv

# Load the environment variables from the specified .env file
load_dotenv('keys.env')

def main():
    host = "https://clob.polymarket.com"
    key = os.getenv("PK")
    chain_id = 137  # Polygon Mainnet chain ID

    # Ensure the private key is loaded correctly
    if not key:
        raise ValueError("Private key not found. Please set PK in the environment variables.")

    # Load API credentials from the environment variables
    creds = ApiCreds(
        api_key=os.getenv("API_KEY"),
        api_secret=os.getenv("SECRET"),
        api_passphrase=os.getenv("PASSPHRASE"),
    )

    # Initialize the client with your private key and API credentials
    client = ClobClient(host, key=key, chain_id=chain_id, creds=creds)

    # Retrieve existing API keys associated with the account
    try:
        api_keys = client.get_api_keys()
        print("Existing API Keys:", api_keys)
    except Exception as e:
        print("Error fetching API keys:", e)

if __name__ == "__main__":
    main()
