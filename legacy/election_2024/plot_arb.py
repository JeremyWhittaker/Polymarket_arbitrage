import os
import subprocess
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import pytz
import logging
from strategies import trades
import json
# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

plot_dir = "./plots/"
os.makedirs(plot_dir, exist_ok=True)



def load_market_lookup():
    """
    Loads the market lookup JSON and maps slugs to token IDs based on outcomes.
    """
    with open('./data/market_lookup.json', 'r') as f:
        market_lookup = json.load(f)

    slug_to_token_id = {}
    for market in market_lookup.values():
        slug = market['market_slug']
        slug_to_token_id[slug] = {token['outcome']: token['token_id'] for token in market['tokens']}

    return slug_to_token_id

def run_get_trade_slugs_to_parquet(token_id, market_slug, outcome):
    """
    Runs the get_trade_slugs_to_parquet.py script with the specified arguments to fetch and save timeseries data.
    """
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'get_trade_slugs_to_parquet.py')
    try:
        result = subprocess.run(["python3", script_path, token_id, market_slug, outcome], capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"Error running get_trade_slugs_to_parquet.py: {result.stderr}")
            raise RuntimeError("Failed to run get_trade_slugs_to_parquet.py")
        logging.info("get_trade_slugs_to_parquet.py ran successfully.")
    except Exception as e:
        logging.exception("Failed to run the script.")

def process_and_fetch_data(trade_slug, outcome):
    """
    Fetches the token_id for the given trade_slug and outcome and runs the get_trade_slugs_to_parquet function.
    """
    market_lookup = load_market_lookup()  # Load the market lookup from JSON

    if trade_slug in market_lookup and outcome in market_lookup[trade_slug]:
        token_id = market_lookup[trade_slug][outcome]
        run_get_trade_slugs_to_parquet(token_id, trade_slug, outcome)
    else:
        logging.error(f"Slug '{trade_slug}' or outcome '{outcome}' not found in the lookup.")



def plot_parquet(file_path):
    """
    Plots the data from a Parquet file and saves the plot as an HTML file.
    """
    try:
        df = pd.read_parquet(file_path)
        if 'timestamp' in df.columns and 'price' in df.columns:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['price'], mode='lines',
                                     name=os.path.basename(file_path).replace('.parquet', '')))
            output_file = os.path.join(plot_dir, os.path.basename(file_path).replace('.parquet', '.html'))
            fig.write_html(output_file)
            logging.info(f"Plot saved to {output_file}")
        else:
            logging.warning(f"Data in {file_path} does not contain expected columns 'timestamp' and 'price'")
    except Exception as e:
        logging.exception(f"Failed to plot data from Parquet file {file_path}")


def plot_trade_sides(trade_name, subtitle, side_a_trades=None, side_b_trades=None, positions=None, method="balanced", timezone='America/Phoenix', plot_last_x_days=14):
    """
    Plots cumulative data for two sides of a trade, the arbitrage percentage, and individual slug data.
    Includes a subtitle for the chart. Only the last plot_last_x_days days of data will be plotted.

    :param trade_name: The name of the trade.
    :param subtitle: The subtitle or hypothesis to be displayed below the title.
    :param side_a_trades: List of tuples for side A trades, where each tuple is (slug, 'Yes' or 'No').
    :param side_b_trades: List of tuples for side B trades, where each tuple is (slug, 'Yes' or 'No').
    :param positions: List of positions (slug, 'No') for the 'all_no' method.
    :param method: The method to use for arbitrage calculation ("balanced" or "all_no").
    :param timezone: The desired timezone for displaying timestamps. Default is 'America/Phoenix'.
    :param plot_last_x_days: The number of days of data to display in the plot. Default is 14.
    """

    def convert_timezone(df, timezone):
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df['timestamp'] = df['timestamp'].dt.tz_convert(timezone)
        return df

    def truncate_data_for_plotting(df, plot_last_x_days):
        if not df.empty:
            last_timestamp = df['timestamp'].max()
            cutoff_timestamp = last_timestamp - pd.Timedelta(days=plot_last_x_days)
            df = df[df['timestamp'] >= cutoff_timestamp]
        return df

    def aggregate_side(trades):
        combined_df = pd.DataFrame()
        missing_files = []
        missing_columns = []

        for slug, outcome in trades:
            file_path = f"./data/historical/{slug}_{outcome}.parquet"
            if os.path.exists(file_path):
                df = pd.read_parquet(file_path)
                if 'timestamp' in df.columns and 'price' in df.columns:
                    df = convert_timezone(df, timezone)
                    df = df.drop_duplicates(subset='timestamp')
                    df = df.set_index('timestamp').resample('min').ffill().reset_index()

                    if combined_df.empty:
                        combined_df = df[['timestamp', 'price']].copy()
                    else:
                        combined_df = pd.merge(combined_df, df[['timestamp', 'price']], on='timestamp', how='outer')
                        combined_df['price'] = combined_df['price_x'].fillna(0) + combined_df['price_y'].fillna(0)
                        combined_df = combined_df[['timestamp', 'price']]
                else:
                    missing_columns.append(file_path)
                    print(f"Data in {file_path} does not contain expected columns 'timestamp' and 'price'")
                    return None, missing_files, missing_columns
            else:
                missing_files.append(file_path)
                print(f"File {file_path} does not exist.")
                return None, missing_files, missing_columns

        return truncate_data_for_plotting(combined_df, plot_last_x_days), missing_files, missing_columns

    def aggregate_positions(positions):
        combined_df = pd.DataFrame()
        missing_files = []
        missing_columns = []

        for slug, outcome in positions:
            file_path = f"./data/historical/{slug}_{outcome}.parquet"
            if os.path.exists(file_path):
                df = pd.read_parquet(file_path)
                if 'timestamp' in df.columns and 'price' in df.columns:
                    df = convert_timezone(df, timezone)
                    df = df.drop_duplicates(subset='timestamp')
                    df = df.set_index('timestamp').resample('min').ffill().reset_index()
                    df = df.rename(columns={'price': f'price_{slug}'})

                    if combined_df.empty:
                        combined_df = df
                    else:
                        combined_df = pd.merge(combined_df, df, on='timestamp', how='outer')

                else:
                    missing_columns.append(file_path)
                    print(f"Data in {file_path} does not contain expected columns 'timestamp' and 'price'")
                    return None, missing_files, missing_columns
            else:
                missing_files.append(file_path)
                print(f"File {file_path} does not exist.")
                return None, missing_files, missing_columns

        combined_df = combined_df.dropna()
        return truncate_data_for_plotting(combined_df, plot_last_x_days), missing_files, missing_columns

    def calculate_arbitrage_balanced(combined_df):
        combined_df['total_cost'] = combined_df['price_a'] + combined_df['price_b']
        combined_df['arb_percentage'] = (1 - combined_df['total_cost']) * 100

    def calculate_arbitrage_all_no(positions_df):
        if positions_df.empty:
            return pd.DataFrame(), "No data available"

        price_columns = positions_df.columns.drop('timestamp')
        no_prices_df = 1 - positions_df[price_columns]
        arb_percentages = []

        for _, row in positions_df.iterrows():
            min_no_price = no_prices_df.loc[row.name].min()
            total_winnings = no_prices_df.loc[row.name].sum() - min_no_price
            arb_percentage = (total_winnings - (1 - min_no_price)) * 100
            arb_percentages.append(arb_percentage)

        positions_df['arb_percentage'] = arb_percentages
        return positions_df, None

    def calculate_bollinger_bands(series, window=2880, num_std_dev=1):
        rolling_mean = series.rolling(window=window).mean()
        rolling_std = series.rolling(window=window).std()
        upper_band = rolling_mean + (rolling_std * num_std_dev)
        lower_band = rolling_mean - (rolling_std * num_std_dev)
        return rolling_mean, upper_band, lower_band

    def plot_individual_slugs(trades, fig, start_row, color):
        for i, (slug, outcome) in enumerate(trades):
            file_path = f"./data/historical/{slug}_{outcome}.parquet"
            print(f"Processing individual slug: {file_path}")
            if os.path.exists(file_path):
                df = pd.read_parquet(file_path)
                if 'timestamp' in df.columns and 'price' in df.columns:
                    df = df.drop_duplicates(subset='timestamp')

                    # Convert the timezone for the slug data
                    df = convert_timezone(df, timezone)

                    df = df.set_index('timestamp').resample('min').ffill().reset_index()

                    df = truncate_data_for_plotting(df, plot_last_x_days)

                    df['rolling_mean'], df['bollinger_upper'], df['bollinger_lower'] = calculate_bollinger_bands(
                        df['price'])

                    fig.add_trace(go.Scatter(
                        x=df['timestamp'],
                        y=df['price'],
                        mode='lines',
                        name=f"{slug} ({outcome})",
                        line=dict(width=1, color=color),
                        showlegend=False
                    ), row=start_row + i, col=1)

                    fig.add_trace(go.Scatter(
                        x=df['timestamp'],
                        y=df['bollinger_upper'],
                        mode='lines',
                        name=f"{slug} ({outcome}) Upper BB",
                        line=dict(width=1, color='gray'),
                        showlegend=False
                    ), row=start_row + i, col=1)

                    fig.add_trace(go.Scatter(
                        x=df['timestamp'],
                        y=df['bollinger_lower'],
                        mode='lines',
                        name=f"{slug} ({outcome}) Lower BB",
                        line=dict(width=1, color='gray'),
                        fill='tonexty',
                        fillcolor='rgba(128, 128, 128, 0.2)',
                        showlegend=False
                    ), row=start_row + i, col=1)
                else:
                    print(f"Data in {file_path} does not contain expected columns 'timestamp' and 'price'")
            else:
                print(f"File {file_path} does not exist.")

    def add_final_value_marker(fig, df, row, col, line_name, value_column, secondary_y=False):
        """
        Adds the final value marker for the specified line on the plot.

        Args:
            fig: The Plotly figure object.
            df: The dataframe containing the data.
            row: The subplot row index.
            col: The subplot column index.
            line_name: The name of the line.
            value_column: The column to plot the final value.
            secondary_y: Whether the marker should be plotted on the secondary y-axis.
        """
        if not df.empty and value_column in df.columns:
            last_value = df.iloc[-1]

            # Add the final value marker (dot)
            fig.add_trace(go.Scatter(
                x=[last_value['timestamp']],
                y=[last_value[value_column]],
                mode='markers+text',
                marker=dict(size=10, color='red'),
                text=[f"{last_value[value_column]:.4f}"],
                textposition="middle right",
                name=f"Final {line_name} Value",
                showlegend=False
            ), row=row, col=col, secondary_y=secondary_y)

            # Draw a line extending from the final value to the right margin of the plot
            fig.add_trace(go.Scatter(
                x=[last_value['timestamp'], df['timestamp'].max() + pd.Timedelta(days=0.1)],
                y=[last_value[value_column], last_value[value_column]],
                mode='lines',
                line=dict(dash='dash', color='red'),
                showlegend=False
            ), row=row, col=col, secondary_y=secondary_y)

            print(f"'{line_name}' - Final Value: {last_value[value_column]}")

    def prepare_data(method, side_a_trades, side_b_trades, positions):
        if method == "balanced":
            side_a_df, side_a_missing_files, side_a_missing_columns = aggregate_side(side_a_trades)
            side_b_df, side_b_missing_files, side_b_missing_columns = aggregate_side(side_b_trades)

            if side_a_df is None or side_b_df is None or side_a_df.empty or side_b_df.empty:
                print(f"Skipping plot due to missing or insufficient data.")
                return None, None

            combined_df = pd.merge(side_a_df, side_b_df, on='timestamp', how='outer', suffixes=('_a', '_b'))
            calculate_arbitrage_balanced(combined_df)
            num_subplots = 2 + len(side_a_trades) + len(side_b_trades)

        elif method == "all_no":
            positions_df, positions_missing_files, positions_missing_columns = aggregate_positions(positions)

            if positions_df is None or positions_df.empty:
                print(f"Skipping plot due to missing or insufficient data.")
                return None, None

            combined_df, error = calculate_arbitrage_all_no(positions_df)
            if error:
                print(error)
                return None, None

            num_subplots = 1 + len(positions)

        return combined_df, num_subplots

    def create_subplots_layout(method, num_subplots, trade_name, side_a_trades, side_b_trades, positions):
        if method == "balanced":
            fig = make_subplots(
                rows=num_subplots,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.02,
                subplot_titles=(
                        [f"Arbitrage Percentage", f"{trade_name} - Side A vs. Side B"] +
                        [f"Side A: {slug} ({outcome})" for slug, outcome in side_a_trades] +
                        [f"Side B: {slug} ({outcome})" for slug, outcome in side_b_trades]
                ),
                specs=[[{"secondary_y": False}] * 1] +
                      [[{"secondary_y": True}] * 1] +
                      [[{"secondary_y": False}] * 1 for _ in range(num_subplots - 2)]
            )
        elif method == "all_no":
            fig = make_subplots(
                rows=num_subplots,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.02,
                subplot_titles=(
                        [f"Arbitrage Percentage"] +
                        [f"Position: {slug} ({outcome})" for slug, outcome in positions]
                ),
                specs=[[{"secondary_y": False}] * 1] +
                      [[{"secondary_y": False}] * 1 for _ in range(num_subplots - 1)]
            )
        return fig

    def add_balanced_traces(fig, combined_df, side_a_trades, side_b_trades, trade_name):
        # Calculate Bollinger Bands for Arbitrage Percentage
        combined_df['rolling_mean'], combined_df['bollinger_upper'], combined_df[
            'bollinger_lower'] = calculate_bollinger_bands(combined_df['arb_percentage'])

        # Plot Arbitrage Percentage
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['arb_percentage'],
            mode='lines',
            name='Arbitrage Percentage',
            line=dict(width=2, color='green'),
            showlegend=False
        ), row=1, col=1)

        # Plot Bollinger Bands around Arbitrage Percentage
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['bollinger_upper'],
            mode='lines',
            name='Bollinger Upper Band',
            line=dict(width=1, color='gray'),
            showlegend=False
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['bollinger_lower'],
            mode='lines',
            name='Bollinger Lower Band',
            line=dict(width=1, color='gray'),
            fill='tonexty',
            fillcolor='rgba(128, 128, 128, 0.2)',
            showlegend=False
        ), row=1, col=1)

        # Add the final value marker
        add_final_value_marker(fig, combined_df, row=1, col=1, line_name="Arbitrage Percentage",
                               value_column='arb_percentage')

        # Plot cumulative Side A price
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['price_a'],
            mode='lines',
            name=f'{trade_name} - Side A (Cumulative)',
            line=dict(width=2, color='blue'),
            showlegend=False
        ), row=2, col=1, secondary_y=False)

        # Plot cumulative Side B price
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['price_b'],
            mode='lines',
            name=f'{trade_name} - Side B (Cumulative)',
            line=dict(width=2, color='red'),
            showlegend=False
        ), row=2, col=1, secondary_y=True)

        # Add final value markers for Side A and Side B
        add_final_value_marker(fig, combined_df, row=2, col=1, line_name="Side A Price", value_column='price_a')
        add_final_value_marker(fig, combined_df, row=2, col=1, line_name="Side B Price", value_column='price_b', secondary_y=True)

        # Plot individual slugs for Side A and Side B
        for i, (slug, outcome) in enumerate(side_a_trades):
            plot_individual_slugs([(slug, outcome)], fig, start_row=3 + i, color='blue')

        for i, (slug, outcome) in enumerate(side_b_trades):
            plot_individual_slugs([(slug, outcome)], fig, start_row=3 + len(side_a_trades) + i, color='red')

    def add_all_no_traces(fig, combined_df, positions):
        # Calculate Bollinger Bands for Arbitrage Percentage
        combined_df['rolling_mean'], combined_df['bollinger_upper'], combined_df[
            'bollinger_lower'] = calculate_bollinger_bands(combined_df['arb_percentage'])

        # Plot Arbitrage Percentage
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['arb_percentage'],
            mode='lines',
            name='Arbitrage Percentage',
            line=dict(width=2, color='green'),
            showlegend=False
        ), row=1, col=1)

        # Plot Bollinger Bands around Arbitrage Percentage
        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['bollinger_upper'],
            mode='lines',
            name='Bollinger Upper Band',
            line=dict(width=1, color='gray'),
            showlegend=False
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=combined_df['timestamp'],
            y=combined_df['bollinger_lower'],
            mode='lines',
            name='Bollinger Lower Band',
            line=dict(width=1, color='gray'),
            fill='tonexty',
            fillcolor='rgba(128, 128, 128, 0.2)',
            showlegend=False
        ), row=1, col=1)

        # Add the final value marker for Arbitrage Percentage
        add_final_value_marker(fig, combined_df, row=1, col=1, line_name="Arbitrage Percentage",
                               value_column='arb_percentage')

        # Plot individual slugs for positions
        for i, (slug, outcome) in enumerate(positions):
            plot_individual_slugs([(slug, outcome)], fig, start_row=2 + i, color='blue')

    # Main Execution Flow
    combined_df, num_subplots = prepare_data(method, side_a_trades, side_b_trades, positions)

    # Check if the combined DataFrame is None or empty
    if combined_df is None or combined_df.empty:
        print(f"Skipping plot due to missing or insufficient data for trade: {trade_name}")
        return  # Exit the function if no data is available

    combined_df.to_csv('./data/combined_df.csv')

    if combined_df is None:
        return  # Exit if no data was returned

    fig = create_subplots_layout(method, num_subplots, trade_name, side_a_trades, side_b_trades, positions)

    if method == "balanced":
        # Add traces and plot details for balanced method
        add_balanced_traces(fig, combined_df, side_a_trades, side_b_trades, trade_name)

    elif method == "all_no":
        # Add traces and plot details for all_no method
        add_all_no_traces(fig, combined_df, positions)

    last_arb_percentage = combined_df['arb_percentage'].iloc[-1] if not combined_df['arb_percentage'].empty else 'NA'
    title_with_percentage = f"<b>{trade_name}</b> - Final Arb %: {last_arb_percentage:.2f}"

    fig.update_layout(
        title={
            'text': f"{title_with_percentage}<br><sup>{subtitle}</sup>",
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top'
        },
        xaxis_title='Timestamp',
        yaxis_title='Cumulative Price',
        template='plotly_white',
        height=300 * num_subplots,
        margin=dict(t=100),
        showlegend=False  # Ensuring legend is not shown
    )

    output_file = os.path.join(plot_dir, f"{trade_name.replace(' ', '_')}.html")
    fig.write_html(output_file)
    print(f"Trade plot saved to {output_file}")

def main():
    # Step 1: Process each trade and fetch data if necessary
    for trade in trades:
        print(f'Processing trade: {trade}')

        # Process side A trades
        if "side_a_trades" in trade:
            for slug, outcome in trade["side_a_trades"]:
                process_and_fetch_data(slug, outcome)

        # Process side B trades
        if "side_b_trades" in trade:
            for slug, outcome in trade["side_b_trades"]:
                process_and_fetch_data(slug, outcome)

        # Process 'all_no' method positions
        if trade["method"] == "all_no" and "positions" in trade:
            for slug, outcome in trade["positions"]:
                process_and_fetch_data(slug, outcome)

        # Now proceed to plotting after fetching the data
        if trade["method"] == "balanced":
            plot_trade_sides(trade["trade_name"], trade["subtitle"], trade["side_a_trades"], trade["side_b_trades"],
                             method=trade["method"])
        elif trade["method"] == "all_no":
            plot_trade_sides(trade["trade_name"], trade["subtitle"], positions=trade["positions"],
                             method=trade["method"])
        else:
            raise ValueError(f"Unknown method '{trade['method']}' for trade '{trade['trade_name']}'.")


def main_loop(interval_minutes=5):
    """
    Runs the main function in a loop every `interval_minutes`.
    """
    while True:
        main()
        print(f"Waiting for {interval_minutes} minutes before next run...")
        time.sleep(interval_minutes * 60)  # Convert minutes to seconds


if __name__ == "__main__":
    # Run the main loop with the desired interval (default is 10 minutes)
    main_loop()