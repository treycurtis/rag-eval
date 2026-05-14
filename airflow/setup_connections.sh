#!/bin/bash
# setup_connections.sh
# Run after docker compose up to restore Airflow connections
# Requires env vars in ~/projects/rag-eval/.env

if [[ -f ~/projects/rag-eval/.env ]]; then
  source ~/projects/rag-eval/.env
fi

: ${SNOWFLAKE_USER:?Error: SNOWFLAKE_USER is not set}
: ${SNOWFLAKE_ACCOUNT:?Error: SNOWFLAKE_ACCOUNT is not set}
: ${SNOWFLAKE_WAREHOUSE:?Error: SNOWFLAKE_WAREHOUSE is not set}
: ${SNOWFLAKE_DATABASE:?Error: SNOWFLAKE_DATABASE is not set}
: ${SNOWFLAKE_ROLE:?Error: SNOWFLAKE_ROLE is not set}


docker exec -it airflow-airflow-apiserver-1 airflow connections add snowflake_default \
  --conn-type snowflake \
  --conn-login ${SNOWFLAKE_USER} \
  --conn-host ${SNOWFLAKE_ACCOUNT}.snowflakecomputing.com \
  --conn-schema RAW \
  --conn-extra "{\"account\": \"${SNOWFLAKE_ACCOUNT}\", \"warehouse\": \"${SNOWFLAKE_WAREHOUSE}\", \"database\": \"${SNOWFLAKE_DATABASE}\", \"role\": \"${SNOWFLAKE_ROLE}\", \"private_key_file\": \"/home/airflow/.snowflake/rsa_key.pem\"}"