import argparse
from aegis_cartographer.server import run_server


def main():
    parser = argparse.ArgumentParser(description="Aegis Cartographer MCP Server")
    parser.add_argument(
        "--map-file",
        type=str,
        default="map.json",
        help="Path to the map JSON file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Server host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Server port",
    )
    
    args = parser.parse_args()
    run_server(map_file_path=args.map_file, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
