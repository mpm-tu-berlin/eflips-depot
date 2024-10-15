import logging
import os
import subprocess

logger = logging.getLogger("custom")


def execute_dump(params, output_file='db_updated.sql'):
    # Set the environment variable for the password
    os.environ['PGPASSWORD'] = params['password']

    # Build the pg_dump command
    command = [
        'pg_dump',
        '-h', params['host'],
        '-p', params['port'],
        '-U', params['user'],
        '-d', params['dbname'],
        '--clean',
        '--if-exists',
        '-f', output_file
    ]

    # Execute the command
    try:
        subprocess.run(command, check=True)
        logger.info(f"Database dump successful. Dump saved to {output_file}")
    except subprocess.CalledProcessError as e:
        logger.info(f"Error during pg_dump: {e}")
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

execute_dump(db_params, output_file='db.sql')
