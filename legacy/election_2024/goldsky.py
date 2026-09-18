import boto3
import fastparquet
import io
import pandas as pd

# AWS credentials (if not already configured in the environment)
aws_access_key_id = ''
aws_secret_access_key = ''
aws_region = 'us-west-2'

# S3 bucket details
bucket_name = 'polymarket-goldsky-data'
parquet_file_key = 'user_positions/1725807944-fedcf4ea-c2a2-4c31-a7df-b881ee65a1d9-0-0.parquet'  # Path to your Parquet file in the S3 bucket

# Create a session with AWS S3
s3 = boto3.client('s3',
                  aws_access_key_id=aws_access_key_id,
                  aws_secret_access_key=aws_secret_access_key,
                  region_name=aws_region)

# Download the Parquet file from S3 into memory
obj = s3.get_object(Bucket=bucket_name, Key=parquet_file_key)
file_content = obj['Body'].read()

# Save the file to disk temporarily for checking
local_parquet_path = '/tmp/temp_file.parquet'
with open(local_parquet_path, 'wb') as f:
    f.write(file_content)


# Step 1: Load the Parquet file into a Pandas DataFrame
def load_parquet_to_dataframe(file_path):
    try:
        # Use Fastparquet to read the Parquet file into a Pandas DataFrame
        df = pd.read_parquet(file_path, engine='fastparquet')

        # Print the first few rows of the DataFrame to inspect the data
        print("Data loaded into DataFrame:")
        print(df.head())

        # Optionally, show basic info about the DataFrame
        print("\nDataFrame Info:")
        print(df.info())

    except Exception as e:
        print(f"Error loading Parquet file into DataFrame: {e}")


# Load the Parquet file into a Pandas DataFrame
load_parquet_to_dataframe(local_parquet_path)
