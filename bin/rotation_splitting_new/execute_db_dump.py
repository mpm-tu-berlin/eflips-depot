import os
import subprocess
def execute_dump(db_params, output_file='db_updated.sql'):
    # Set the environment variable for the password
    os.environ['PGPASSWORD'] = db_params['password']

    # Build the pg_dump command
    command = [
        'pg_dump',
        '-h', db_params['host'],
        '-p', db_params['port'],
        '-U', db_params['user'],
        '-d', db_params['dbname'],
        '-f', output_file
    ]

    # Execute the command
    try:
        subprocess.run(command, check=True)
        print(f"Database dump successful. Dump saved to {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error during pg_dump: {e}")
    finally:
        # Clean up the password from the environment
        del os.environ['PGPASSWORD']

db_params = {
    'dbname': 'eflips',
    'user': 'linus',
    'password': '1234',
    'host': 'localhost',
    'port': '5432',
}

execute_dump(db_params, output_file='03b_one_day_op_70.sql')