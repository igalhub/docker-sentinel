import logging
import os
import sys

import docker
import docker.errors
import yaml

from checker.db import init_db, write_results
from checker.docker_checker import check_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SENTINEL_DB_PATH", "results.db")
CONFIG_PATH = os.environ.get("SENTINEL_CONFIG_PATH", "config/settings.yaml")


def main() -> None:
    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("Config file not found: %s", CONFIG_PATH)
        sys.exit(1)

    init_db(DB_PATH)

    try:
        client = docker.from_env()
        results = check_all(client, config)
    except docker.errors.DockerException as exc:
        logger.error("Docker connection failed: %s", exc)
        results = []

    write_results(DB_PATH, results)
    logger.info("Checked %d containers, wrote to %s", len(results), DB_PATH)


if __name__ == "__main__":
    main()
