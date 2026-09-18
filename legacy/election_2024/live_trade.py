import os
import logging
import json
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, BalanceAllowanceParams, AssetType, OrderType, MarketOrderArgs
from dotenv import load_dotenv
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.constants import AMOY


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv('keys.env')

def load_market_lookup():
    with open('./data/market_lookup.json', 'r') as f:
        return json.load(f)

def get_market_ids(market_lookup, slug, outcome):
    for market_id, details in market_lookup.items():
        if details['market_slug'] == slug:
            for token in details['tokens']:
                if token['outcome'].lower() == outcome.lower():
                    return market_id, token['token_id']
    raise ValueError(f"Market slug '{slug}' with outcome '{outcome}' not found in lookup.")

def get_last_trade_price(client, token_id):
    try:
        response = client.get_last_trade_price(token_id=token_id)
        price = response.get('price')
        if price is not None:
            return float(price)
        else:
            raise ValueError("Last trade price not available")
    except Exception as e:
        logging.error(f"Error fetching last trade price for token ID {token_id}: {e}")
        raise

def adjust_price(price):
    """
    Adjust the price by subtracting 1 from the smallest decimal place.
    For example:
    - 50.1 becomes 50.0
    - 50.11 becomes 50.10
    - 50.111 becomes 50.110
    """
    price_str = f"{price:.10f}".rstrip('0').rstrip('.')
    decimal_places = len(price_str.split('.')[1]) if '.' in price_str else 0

    adjustment = 10 ** (-decimal_places)
    adjusted_price = price - adjustment

    return round(adjusted_price, decimal_places)

def set_allowance_if_needed(client, token_id, amount_needed, asset_type):
    try:
        balance_info = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=asset_type,
                token_id=token_id
            )
        )

        available_allowance = sum(float(allowance) for allowance in balance_info['allowances'].values())
        if available_allowance < amount_needed:
            logging.info(f"Allowance insufficient for token {token_id}. Current: {available_allowance}, Required: {amount_needed}")
            # Here is where you update the allowance
            try:
                # Simply updating the allowance, assuming the client library manages how much to set
                response = client.update_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=asset_type,
                        token_id=token_id
                    )
                )
                logging.info(f"Allowance updated successfully: {response}")
            except Exception as e:
                logging.error(f"Error updating allowance: {e}")
                raise
        else:
            logging.info(f"Sufficient allowance already set for token {token_id}")
    except Exception as e:
        logging.error(f"Error setting allowance: {e}")
        raise


def check_balance_and_allowance(client, token_id, amount_needed):
    try:
        collateral = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        conditional = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id = token_id
            )
        )

        logging.debug(f'collateral: {collateral}')
        logging.debug(f'conditional: {conditional}')


        logging.debug(f"Checking balance and allowance for token ID: {token_id}")
        balance_info = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id
            )
        )

        logging.debug(f"Raw Balance and Allowance Info: {balance_info}")

        available_balance = float(balance_info['balance'])
        available_allowance = sum(float(allowance) for allowance in balance_info['allowances'].values())

        logging.info(f"Available balance for token {token_id}: {available_balance}")
        logging.info(f"Available allowance for token {token_id}: {available_allowance}")

        if available_balance < amount_needed:
            logging.error(f"Insufficient balance. Available: {available_balance}, Required: {amount_needed}")
            # return False, available_balance

        if available_allowance < amount_needed:
            logging.error(f"Insufficient allowance. Available: {available_allowance}, Required: {amount_needed} \n Setting allowance...")
            # Call the function to set the allowance
            set_allowance_if_needed(client, token_id, amount_needed, AssetType.CONDITIONAL)

        return True, available_balance

    except Exception as e:
        logging.error(f"Error checking balance and allowance: {e}")
        return False, 0

def place_limit_order(client, market_slug, outcome, amount, side, expiration=None):
    market_lookup = load_market_lookup()
    try:
        market_id, clob_id = get_market_ids(market_lookup, market_slug, outcome)
        logging.debug(f"Market ID: {market_id}, Clob ID: {clob_id} for {market_slug} - {outcome}")

        last_trade_price = get_last_trade_price(client, clob_id)
        adjusted_price = adjust_price(last_trade_price)
        logging.debug(f"Calculated order price: {adjusted_price}")

        total_amount_needed = amount * adjusted_price

        # Check if there is enough balance and allowance
        sufficient_balance, available_balance = check_balance_and_allowance(client, clob_id, total_amount_needed)
        if not sufficient_balance:
            print(f"Insufficient balance/allowance. Available: {available_balance}, Required: {total_amount_needed}")
            return

        # Creating the order using OrderArgs
        order_args = OrderArgs(
            price=adjusted_price,
            size=amount,
            side=BUY if side.lower() == "buy" else SELL,
            token_id=clob_id,
            expiration=expiration,  # Only for GTD orders
            fee_rate_bps=0  # Set this correctly based on your platform's requirement
            # nonce is omitted if not required
        )

        logging.debug(f"Order Arguments: {order_args}")

        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order, OrderType.GTC)
        logging.info(f"Order placed successfully: {response}")

    except Exception as e:
        logging.error(f"Error placing limit order for market {market_slug}: {e}")

def place_market_order(client, market_slug, outcome, amount=1):
    market_lookup = load_market_lookup()
    try:
        market_id, clob_id = get_market_ids(market_lookup, market_slug, outcome)
        logging.debug(f"Market ID: {market_id}, Clob ID: {clob_id} for {market_slug} - {outcome}")

        # Check if there is enough balance and allowance
        sufficient_balance, available_balance = check_balance_and_allowance(client, clob_id, size)
        if not sufficient_balance:
            print(f"Insufficient balance/allowance. Available: {available_balance}, Required: {size}")
            return

        logging.debug(f'Using amount: {amount}')


        # Creating the market order without specifying the price for a true market order
        order_args = MarketOrderArgs(
            token_id=clob_id,
            amount=round(size, 5)  # Ensuring amount precision is compliant
            # No price specified for a true market order
        )

        logging.debug(f"Market Order Arguments before signing: {order_args}")

        # Sign the order
        signed_order = client.create_market_order(order_args)
        logging.debug(f"Signed Order: {signed_order}")

        # Send the order
        response = client.post_order(signed_order)
        logging.info(f"Market order placed successfully: {response}")

    except Exception as e:
        logging.error(f"Error placing market order for market {market_slug}: {e}")


if __name__ == "__main__":
    host = "https://clob.polymarket.com"
    key = os.getenv("PK")
    creds = ApiCreds(
        api_key=os.getenv("API_KEY"),
        api_secret=os.getenv("SECRET"),
        api_passphrase=os.getenv("PASSPHRASE"),
    )
    chain_id = 137  # Polygon Mainnet

    logging.debug(f"Initializing ClobClient.")
    client = ClobClient(host, key=key, chain_id=chain_id, creds=creds)
    logging.info("Client initialized successfully.")

    market_slug = "will-kamala-harris-win-the-2024-us-presidential-election"
    outcome = "No"
    size = 1
    amount = 1
    order_type = "market"  # Change this to "limit" or "market"

    if order_type == "limit":
        place_limit_order(client, market_slug, outcome, size, side="buy")
    elif order_type == "market":
        place_market_order(client, market_slug, outcome, amount=amount)  # Defaults to $1 for market order
