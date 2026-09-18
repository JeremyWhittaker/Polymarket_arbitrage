import os
import pandas as pd
import logging
import argparse
from jinja2 import Template
import subprocess
import json
import time
import subprocess
import time
# Configure logging
logging.basicConfig(level=logging.INFO)



def call_get_polygon_data(wallet_id_or_username):
    """
    Call subprocess to get polygon data by wallet_id or username.
    """
    try:
        logging.info(f"Calling get_polygon_data.py for wallet/username: {wallet_id_or_username}")
        result = subprocess.run(
            ['python3', 'get_polygon_data.py', '--wallets', wallet_id_or_username],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        logging.debug(f"Subprocess stdout: {result.stdout}")
        logging.debug(f"Subprocess stderr: {result.stderr}")
        logging.info(f"Polygon data retrieval completed for {wallet_id_or_username}.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error calling get_polygon_data.py for {wallet_id_or_username}: {e.stderr}")
        return False
    return True

def load_strategies_from_python():
    """
    Load strategies from the strategies.py file.
    """
    try:
        from strategies import trades
        return trades
    except Exception as e:
        logging.error(f"Failed to load strategies from strategies.py: {e}")
        return []

def load_user_data(user_data_path):
    """
    Load user transaction data from Parquet or CSV file.
    """
    try:
        if user_data_path.endswith('.parquet'):
            return pd.read_parquet(user_data_path)
        elif user_data_path.endswith('.csv'):
            return pd.read_csv(user_data_path)
        else:
            raise ValueError("Unsupported file format. Only Parquet and CSV are supported.")
    except Exception as e:
        logging.error(f"Error loading user data: {e}")
        return pd.DataFrame()

def call_get_user_profile(wallet_id):
    """
    Call subprocess to get user profile data by wallet_id.
    """
    if not wallet_id:
        logging.error("No wallet ID provided.")
        return None

    try:
        logging.info(f"Calling subprocess to fetch user profile for wallet ID: {wallet_id}")
        result = subprocess.run(
            ['python3', 'get_user_profile.py', wallet_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            timeout=30
        )
        logging.debug(f"Subprocess stdout: {result.stdout}")
        logging.debug(f"Subprocess stderr: {result.stderr}")
        user_data = json.loads(result.stdout)
        return user_data

    except subprocess.TimeoutExpired:
        logging.error(f"Subprocess timed out when fetching user profile for wallet ID: {wallet_id}")
        return None
    except subprocess.CalledProcessError as e:
        logging.error(f"Subprocess error when fetching user profile for wallet ID {wallet_id}: {e.stderr}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON from subprocess for wallet ID {wallet_id}: {e}")
        return None

def get_username_from_wallet(wallet_id):
    """
    Fetch username corresponding to a wallet ID.
    """
    user_data = call_get_user_profile(wallet_id)
    if user_data and 'username' in user_data:
        return user_data['username']
    else:
        logging.error(f"No username found for wallet ID: {wallet_id}")
        return None
def load_user_data(user_data_path):
    """
    Load user transaction data from Parquet or CSV file.
    """
    try:
        if user_data_path.endswith('.parquet'):
            return pd.read_parquet(user_data_path)
        elif user_data_path.endswith('.csv'):
            return pd.read_csv(user_data_path)
        else:
            raise ValueError("Unsupported file format. Only Parquet and CSV are supported.")
    except Exception as e:
        logging.error(f"Error loading user data: {e}")
        return pd.DataFrame()

def get_last_price_paid(df, market_slug, outcome):
    """
    Get the last price paid for a specific market_slug and outcome.
    """
    filtered_df = df[(df['market_slug'] == market_slug) & (df['outcome'] == outcome)]
    if not filtered_df.empty:
        latest_transaction = filtered_df.sort_values(by='timeStamp_erc1155', ascending=False).iloc[0]
        return latest_transaction['price_paid_per_token']
    else:
        return None
def calculate_total_prices(positions_with_prices):
    """
    Calculate the total price for a list of positions.
    """
    total_price = sum(position['last_price_paid'] for position in positions_with_prices if isinstance(position['last_price_paid'], (int, float)))
    return total_price

def calculate_shares(df, market_slug, outcome):
    """
    Calculate total shares for a given market_slug and outcome.
    Buys add shares, and sells subtract shares.
    """
    filtered_df = df[(df['market_slug'] == market_slug) & (df['outcome'] == outcome)]
    if filtered_df.empty:
        return None  # Return None if no data is found

    # Sum up the shares based on transaction_type
    total_shares = filtered_df.apply(
        lambda row: row['shares'] if row['transaction_type'] == 'buy' else -row['shares'], axis=1).sum()

    return round(total_shares)  # Round to the nearest whole number

def calculate_shares_to_balance(positions_with_shares):
    """
    Calculate the 'shares to balance trade' for each position.
    This will be the difference between the highest number of shares and the shares of the current row.
    If no valid shares exist, set 'shares_to_balance' to 'No Data'.
    """
    valid_shares = [pos['shares'] for pos in positions_with_shares if isinstance(pos['shares'], (int, float))]

    if not valid_shares:
        # If there are no valid shares, set 'shares_to_balance' to 'No Data' for all positions
        for pos in positions_with_shares:
            pos['shares_to_balance'] = "No Data"
        return

    max_shares = max(valid_shares)

    for pos in positions_with_shares:
        if isinstance(pos['shares'], (int, float)):
            pos['shares_to_balance'] = max_shares - pos['shares']
        else:
            pos['shares_to_balance'] = "No Data"

def calculate_total_average_prices(positions_with_prices):
    """
    Calculate the total of average prices for a list of positions.
    """
    total_avg_price = sum(position['average_price_paid'] for position in positions_with_prices if isinstance(position['average_price_paid'], (int, float)))
    return total_avg_price



def calculate_average_price(df, market_slug, outcome):
    """
    Calculate the average price paid for a given market_slug and outcome.
    Only buy transactions are considered.
    """
    filtered_df = df[
        (df['market_slug'] == market_slug) & (df['outcome'] == outcome) & (df['transaction_type'] == 'buy')]

    if filtered_df.empty:
        return None  # Return None if no buy transactions found

    # Calculate total amount paid and total shares bought
    total_amount_paid = (filtered_df['price_paid_per_token'] * filtered_df['shares']).sum()
    total_shares_bought = filtered_df['shares'].sum()

    if total_shares_bought == 0:
        return None  # Avoid division by zero if no shares bought

    # Calculate the average price paid
    average_price_paid = total_amount_paid / total_shares_bought
    return round(average_price_paid, 3)  # Return rounded to 3 decimals

def generate_html_summary(trades, user_data, output_path):
    """
    Generate an HTML file summarizing the last price paid, total shares, shares to balance, and average price paid for each trade.
    """
    html_template = """
    <html>
    <head>
        <title>Trade Summary</title>
        <style>
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 8px 12px; border: 1px solid #ccc; text-align: left; }
            th { background-color: #f4f4f4; }
        </style>
    </head>
    <body>
        <h1>Summary of Last Prices Paid for Trades</h1>
        {% for trade in trades %}
            <h2>{{ trade.trade_name }}</h2>
            <p>{{ trade.subtitle }}</p>
            {% if trade.total_a is not none or trade.total_b is not none %}
                <p><strong>
                    {% if trade.total_a is not none %} Total Price for Side A: {{ '%.3f' % trade.total_a }} {% endif %}
                    {% if trade.total_b is not none %} Total Price for Side B: {{ '%.3f' % trade.total_b }} {% endif %}
                    {% if trade.total_price is not none %} Total Price for Both Sides: {{ '%.3f' % trade.total_price }} {% endif %}
                </strong></p>
                <p><strong>
                    {% if trade.total_avg_a is not none %} Total of Average Prices for Side A: {{ '%.3f' % trade.total_avg_a }} {% endif %}
                    {% if trade.total_avg_b is not none %} Total of Average Prices for Side B: {{ '%.3f' % trade.total_avg_b }} {% endif %}
                    {% if trade.total_avg_price is not none %} Total of Average Prices for Both Sides: {{ '%.3f' % trade.total_avg_price }} {% endif %}
                </strong></p>
            {% endif %}
            <table>
                <thead>
                    <tr>
                        <th>Market Slug</th>
                        <th>Outcome</th>
                        <th>Last Price Paid</th>
                        <th>Shares</th>
                        <th>Shares to Balance Trade</th>
                        <th>Average Price Paid</th>
                    </tr>
                </thead>
                <tbody>
                    {% for position in trade.positions %}
                        <tr>
                            <td>{{ position.slug }}</td>
                            <td>{{ position.outcome }}</td>
                            <td>{{ position.last_price_paid }}</td>
                            <td>{{ position.shares }}</td>
                            <td>{{ position.shares_to_balance }}</td>
                            <td>{{ position.average_price_paid }}</td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% endfor %}
    </body>
    </html>
    """

    # Prepare data to be passed to the template
    processed_trades_complete = []
    processed_trades_incomplete = []

    for trade in trades:
        positions_with_prices = []
        has_missing_data = False
        total_a = None
        total_b = None
        total_avg_a = None
        total_avg_b = None
        valid_last_price_paid = False  # Track if there's any valid last price

        if trade.get('positions'):  # Handle "all_no" method
            for slug, outcome in trade['positions']:
                last_price_paid = get_last_price_paid(user_data, slug, outcome)
                shares = calculate_shares(user_data, slug, outcome)  # Calculate shares
                average_price_paid = calculate_average_price(user_data, slug, outcome)  # Calculate average price paid

                if last_price_paid is not None:
                    valid_last_price_paid = True  # Mark as valid data if any valid price is found

                if last_price_paid is None or shares is None:
                    has_missing_data = True

                positions_with_prices.append({
                    'slug': slug,
                    'outcome': outcome,
                    'last_price_paid': last_price_paid if last_price_paid is not None else "No Data",
                    'shares': shares if shares is not None else "No Data",
                    'average_price_paid': average_price_paid if average_price_paid is not None else "No Data"
                })

            # If no valid last price paid for all positions, skip the trade
            if not valid_last_price_paid:
                continue  # Skip this trade

            # Calculate 'shares to balance trade' for all positions
            calculate_shares_to_balance(positions_with_prices)

        elif trade.get('side_a_trades') and trade.get('side_b_trades'):  # Handle "balanced" method
            positions_with_prices_a = []
            positions_with_prices_b = []

            for slug, outcome in trade['side_a_trades']:
                last_price_paid = get_last_price_paid(user_data, slug, outcome)
                shares = calculate_shares(user_data, slug, outcome)  # Calculate shares
                average_price_paid = calculate_average_price(user_data, slug, outcome)  # Calculate average price paid

                if last_price_paid is not None:
                    valid_last_price_paid = True  # Mark as valid data if any valid price is found

                if last_price_paid is None or shares is None:
                    has_missing_data = True

                positions_with_prices_a.append({
                    'slug': slug,
                    'outcome': outcome,
                    'last_price_paid': last_price_paid if last_price_paid is not None else "No Data",
                    'shares': shares if shares is not None else "No Data",
                    'average_price_paid': average_price_paid if average_price_paid is not None else "No Data"
                })

            for slug, outcome in trade['side_b_trades']:
                last_price_paid = get_last_price_paid(user_data, slug, outcome)
                shares = calculate_shares(user_data, slug, outcome)  # Calculate shares
                average_price_paid = calculate_average_price(user_data, slug, outcome)  # Calculate average price paid

                if last_price_paid is not None:
                    valid_last_price_paid = True  # Mark as valid data if any valid price is found

                if last_price_paid is None or shares is None:
                    has_missing_data = True

                positions_with_prices_b.append({
                    'slug': slug,
                    'outcome': outcome,
                    'last_price_paid': last_price_paid if last_price_paid is not None else "No Data",
                    'shares': shares if shares is not None else "No Data",
                    'average_price_paid': average_price_paid if average_price_paid is not None else "No Data"
                })

            # If no valid last price paid for all positions, skip the trade
            if not valid_last_price_paid:
                continue  # Skip this trade

            positions_with_prices = positions_with_prices_a + positions_with_prices_b

            # Calculate 'shares to balance trade' for both sides
            calculate_shares_to_balance(positions_with_prices_a)
            calculate_shares_to_balance(positions_with_prices_b)

            total_a = calculate_total_prices(positions_with_prices_a)
            total_b = calculate_total_prices(positions_with_prices_b)

            # Calculate the total of average prices for Side A and Side B
            total_avg_a = calculate_total_average_prices(positions_with_prices_a)
            total_avg_b = calculate_total_average_prices(positions_with_prices_b)

        total_price = calculate_total_prices(positions_with_prices) if positions_with_prices else None
        total_avg_price = calculate_total_average_prices(positions_with_prices) if positions_with_prices else None

        processed_trade = {
            'trade_name': trade['trade_name'],
            'subtitle': trade['subtitle'],
            'positions': positions_with_prices,
            'total_a': total_a if total_a else None,
            'total_b': total_b if total_b else None,
            'total_price': total_price if total_price else None,
            'total_avg_a': total_avg_a if total_avg_a else None,
            'total_avg_b': total_avg_b if total_avg_b else None,
            'total_avg_price': total_avg_price if total_avg_price else None
        }

        if has_missing_data:
            processed_trades_incomplete.append(processed_trade)
        else:
            processed_trades_complete.append(processed_trade)

    # Sort trades: Complete trades at the top, incomplete trades at the bottom
    processed_trades = processed_trades_complete + processed_trades_incomplete

    # Use Jinja2 to render the template
    template = Template(html_template)
    rendered_html = template.render(trades=processed_trades)

    # Write to the output HTML file
    with open(output_path, 'w') as f:
        f.write(rendered_html)

    logging.info(f"HTML summary saved to {output_path}")


def process_wallet_data_for_user(username):
    """
    Call the first program to process wallet data for a given user.
    """
    try:
        command = ['python3', 'first_program.py', '--wallets', username]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        logging.info(f"Wallet data processing completed for user {username}.")
        logging.debug(f"Subprocess output: {result.stdout}")
        logging.debug(f"Subprocess error (if any): {result.stderr}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error processing wallet data for {username}: {e.stderr}")
        return False
    return True

def main(wallet_id_or_username, strategies_file):
    """
    Main function to process the user trades and strategies.
    """
    # Handle input to determine if it's a wallet ID or username
    if wallet_id_or_username is None:
        logging.info("No wallet ID or username provided, defaulting to 'JeremyRWhittaker'.")
        username = "JeremyRWhittaker"
        wallet_address = None
    elif wallet_id_or_username.startswith("0x"):  # It's a wallet address
        logging.info(f"Input detected as wallet ID: {wallet_id_or_username}")
        wallet_address = wallet_id_or_username
        # Attempt to fetch the username from the wallet address
        username = get_username_from_wallet(wallet_address)
        if username is None:
            logging.error(f"Could not resolve username from wallet address: {wallet_address}")
            return
    else:
        logging.info(f"Input detected as username: {wallet_id_or_username}")
        username = wallet_id_or_username
        wallet_address = None

    # Check if we found a username
    if username is None:
        logging.error(f"Could not find a valid username for {wallet_id_or_username}. Exiting.")
        return

    # Update user's trade data by running get_polygon_data.py as a subprocess
    try:
        logging.info(f"Updating trade data for user: {username} (wallet: {wallet_address})")

        cmd = [
            'python', 'get_polygon_data.py',
            '--wallets', wallet_address or username,  # Use wallet address if available, otherwise username
            '--skip-leaderboard',
            '--no-plot'
        ]
        subprocess.run(cmd, check=True)
        logging.info(f"Successfully updated trade data for user: {username}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error updating trade data: {e}", exc_info=True)
        return

    # Load user transaction data
    user_data_file = f'./data/user_trades/{username}_enriched_transactions.parquet'
    user_data = load_user_data(user_data_file)

    if user_data.empty:
        logging.error(f"No transaction data found for user: {username}. Exiting.")
        return

    # Load strategies and generate HTML summary
    trades = load_strategies_from_python()
    if not trades:
        logging.error(f"No valid strategies found in {strategies_file}. Exiting.")
        return

    output_html_file = f'./strategies/{username}_last_traded_price.html'
    generate_html_summary(trades, user_data, output_html_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a trade summary HTML for a user.")
    parser.add_argument('wallet_id_or_username', nargs='?', default=None,
                        help="Username or wallet ID for which to generate the summary (used to locate transaction file).")
    parser.add_argument('strategies_file', nargs='?', default="./data/strategies.py",
                        help="Path to the strategies file (Python).")

    args = parser.parse_args()

    main(args.wallet_id_or_username, args.strategies_file)