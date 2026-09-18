from dotenv import load_dotenv
import os
from py_clob_client.client import ClobClient
from py_clob_client.constants import AMOY

# Load environment variables from the specific 'keys.env' file
load_dotenv('keys.env')


def main():
    host = "https://clob.polymarket.com"  # Replace with the correct Polymarket CLOB endpoint
    key = os.getenv("PK")

    # Verify if the private key is loaded correctly
    if not key:
        print("Private key not found! Please set PK in the environment variables.")
        return

    print(f"Private Key: {key[:10]}...")  # Only print the first 10 characters for security

    chain_id = AMOY  # Replace with the actual chain ID if needed (Polygon Mainnet = 137)

    # Initialize the ClobClient with the private key and chain ID
    try:
        client = ClobClient(host, key=key, chain_id=chain_id)
        print("Client initialized successfully.")
    except Exception as e:
        print(f"Error initializing client: {e}")
        return

    # Derive the API key based on the private key and other parameters
    try:
        derived_api_creds = client.derive_api_key()
        print("Derived API Key:", derived_api_creds.api_key)
        print("Secret:", derived_api_creds.api_secret)
        print("Passphrase:", derived_api_creds.api_passphrase)
    except Exception as e:
        print("Error deriving API key:", e)


if __name__ == "__main__":
    main()
