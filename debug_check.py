
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime
import decimal
import json

# Setup env vars mimicking app.py (if needed, but usually local env handles it or we rely on defaults)
# Assuming DATABASE_URL is set in environment or default works if local. 
# For the user, I'll use the default if not set, but they seem to use Render so env vars are there? 
# Wait, run_command runs on the user's machine (local), but they are deploying to Render.
# The user's local machine might not have the DB.
# BUT, the user is running these commands? No, the user provided me access to their file system "c:\Users...".
# The user is running locally?
# The user provided logs from Render: "/opt/render/project/src/...".
# So the production is on Render.
# Running a script LOCALLY on "c:\Users..." might fail if they don't have the DB locally.
# However, if I can't reproduce it locally, I can't debug it easily unless I add more logging to app.py and ask them to deploy.
# But I can check if the python code is syntactically correct and imports are fine.

# Wait, if `debug_serialization.py` runs locally, it proves the code is valid Python. 
# Attempting to verify the syntax and logic.

def serialize_data(data):
    """Converte objetos não serializáveis (datetime, Decimal) para string/float"""
    if isinstance(data, list):
        return [serialize_data(item) for item in data]
    elif isinstance(data, dict):
        return {key: serialize_data(value) for key, value in data.items()}
    elif isinstance(data, (datetime.datetime, datetime.date)):
        return data.isoformat()
    elif isinstance(data, decimal.Decimal):
        return float(data)
    return data

print("Syntax check passed.")
print("Testing serialization logic...")

test_data = {
    "dec": decimal.Decimal('10.5'),
    "date": datetime.datetime.now(),
    "list": [decimal.Decimal('1.1'), datetime.date(2023, 1, 1)]
}

try:
    serialized = serialize_data(test_data)
    print("Serialization success:", json.dumps(serialized))
except Exception as e:
    print("Serialization failed:", e)
