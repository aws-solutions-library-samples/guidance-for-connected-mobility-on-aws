import os
import json
import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session


aws_secret = boto3.client("secretsmanager")
aws_iot = boto3.client("iot")

RDS_PROXY_ENDPOINT = os.getenv("RDS_PROXY_ENDPOINT", "")
CREDENTIAL_SECRET_NAME = os.getenv("CREDENTIAL_SECRET_NAME", "")
DATABASE_NAME = os.getenv("DATABASE_NAME", "")

SECRET_VALUE = json.loads(
    aws_secret.get_secret_value(SecretId=CREDENTIAL_SECRET_NAME)["SecretString"]
)

DB_USER = SECRET_VALUE["username"]
DB_PASSWORD = SECRET_VALUE["password"]

URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{RDS_PROXY_ENDPOINT}/{DATABASE_NAME}?sslmode=require"
ENGINE = create_engine(URL, echo=False)

session_factory = sessionmaker(bind=ENGINE)
Session = scoped_session(session_factory)
session = Session()
