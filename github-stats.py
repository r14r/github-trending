#!/usr/bin/env python3

import os
import sys
import requests
import argparse
import json
from datetime import datetime, timedelta, timezone

try:
    import yaml
except ImportError:
    yaml = None

def parse_date(date_str):
    """Convert 'today', 'yesterday', or YYYY-MM-DD into a valid YYYY-MM-DD string."""
    if not date_str:
        return None
    
    date_input = date_str.strip().lower()
    now_utc = datetime.now(timezone.utc)
    
    if date_input == "today":
        return now_utc.strftime('%Y-%m-%d')
    elif date_input == "yesterday":
        return (now_utc - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        try:
            datetime.strptime(date_input, '%Y-%m-%d')
            return date_input
        except ValueError:
            print(f"Error: Incorrect date format '{date_str}'. Use YYYY-MM-DD, 'today', or 'yesterday'.", file=sys.stderr)
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="github-stats: A CLI to fetch trending and top-starred GitHub repositories.",
        epilog="Examples:\n"
               "  github-stats --trending --date today --append trending.json\n"
               "  github-stats --stars --topic machine-learning --yaml\n"
               "  github-stats --trending --date 2026-10-29 --append stats.yml",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Primary actions
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--trending", action="store_true", help="Fetch trending repos (recently created).")
    action_group.add_argument("--stars", action="store_true", help="Fetch repositories ranked by total stars.")
    
    # Date filters
    parser.add_argument("--all", action="store_true", help="Fetch all-time data.")
    parser.add_argument("--date", type=str, help="Specific start date (YYYY-MM-DD, 'today', 'yesterday').")
    parser.add_argument("--date-from", type=str, dest="date_from", help="Start date for a specific range.")
    parser.add_argument("--date-to", type=str, dest="date_to", help="End date for a specific range.")
    
    # Content filters
    parser.add_argument("--lang", type=str, help="Filter by programming language.")
    parser.add_argument("--topic", type=str, help="Filter by repository topic.")
    parser.add_argument("--limit", type=int, default=10, help="Number of repositories to return (max 100).")
    
    # Formatting and file operations
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument("--json", action="store_true", help="Output raw JSON data.")
    format_group.add_argument("--yaml", action="store_true", help="Output raw YAML data.")
    
    parser.add_argument("--append", type=str, metavar="FILENAME", help="Append/update data in a JSON or YAML file, keyed by date.")
    
    args = parser.parse_args()

    # 1. Ensure token exists
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        print("Run: export GITHUB_TOKEN=\"$(gh auth token)\"", file=sys.stderr)
        sys.exit(1)

    # 2. Build Query String and determine the Dictionary Date Key
    query_parts = []
    
    if args.lang:
        query_parts.append(f"language:{args.lang}")
    if args.topic:
        query_parts.append(f"topic:{args.topic}")

    date_key = "all_time"
    
    if not args.all:
        if args.date_from or args.date_to:
            d_from = parse_date(args.date_from) if args.date_from else "*"
            d_to = parse_date(args.date_to) if args.date_to else "*"
            date_key = f"{d_from}_to_{d_to}"
            query_parts.append(f"created:{d_from}..{d_to}")
        elif args.date:
            d = parse_date(args.date)
            date_key = d
            query_parts.append(f"created:>={d}")
        elif args.trending:
            d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
            date_key = d
            query_parts.append(f"created:>={d}")
            
    query_parts.append("sort:stars-desc")
    search_query_string = " ".join(query_parts)

    # 3. GraphQL Query
    graphql_query = """
    query ($queryString: String!, $limit: Int!) {
      search(query: $queryString, type: REPOSITORY, first: $limit) {
        repositoryCount
        edges {
          node {
            ... on Repository {
              nameWithOwner
              description
              stargazerCount
              primaryLanguage { name }
              url
              createdAt
            }
          }
        }
      }
    }
    """
    
    variables = {"queryString": search_query_string, "limit": min(args.limit, 100)}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 4. Execute Request
    response = requests.post(
        "https://api.github.com/graphql", 
        json={"query": graphql_query, "variables": variables}, 
        headers=headers
    )

    if response.status_code != 200:
        print(f"API Request failed with status {response.status_code}\n{response.text}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    if "errors" in data:
        print("GraphQL returned errors:\n" + json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    # 5. Build the Payload
    search_data = data.get("data", {}).get("search", {})
    clean_repos = [edge["node"] for edge in search_data.get("edges", [])]
    
    payload = {
        "total_matches": search_data.get("repositoryCount", 0),
        "query": search_query_string,
        "repositories": clean_repos
    }

    # 6. Handle File Append/Overwrite Logic
    if args.append:
        file_path = args.append
        # Auto-detect format by extension if flag isn't explicitly provided
        is_yaml = args.yaml or file_path.lower().endswith(('.yml', '.yaml'))
        
        if is_yaml and yaml is None:
            print("Error: 'pyyaml' is not installed. Run 'pip install pyyaml'.", file=sys.stderr)
            sys.exit(1)

        file_data = {}
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    file_data = yaml.safe_load(f) if is_yaml else json.load(f)
                    if not isinstance(file_data, dict):
                        file_data = {}
                except Exception as e:
                    print(f"Warning: Could not parse existing file, starting fresh. ({e})", file=sys.stderr)
                    file_data = {}

        # Replace or add the entry for the target date
        file_data[date_key] = payload

        with open(file_path, 'w') as f:
            if is_yaml:
                yaml.dump(file_data, f, sort_keys=False, allow_unicode=True)
            else:
                json.dump(file_data, f, indent=2)
                
        print(f"✅ Successfully updated data for date '{date_key}' in {file_path}", file=sys.stderr)

    # 7. Output to Terminal (if not appending)
    else:
        if args.yaml:
            if yaml is None:
                print("Error: 'pyyaml' is not installed. Run 'pip install pyyaml'.", file=sys.stderr)
                sys.exit(1)
            print(yaml.dump({date_key: payload}, sort_keys=False, allow_unicode=True))
            
        elif args.json:
            print(json.dumps({date_key: payload}, indent=2))
            
        else:
            print(f"\n🔍 Query: {search_query_string}")
            print(f"📊 Total Matches on GitHub: {payload['total_matches']:,}")
            print(f"Showing top {len(clean_repos)} results:\n" + "-"*50)
            
            for repo in clean_repos:
                stars = repo.get("stargazerCount")
                name = repo.get("nameWithOwner")
                desc = repo.get("description") or "No description provided."
                created = repo.get("createdAt", "").split("T")[0]
                lang_node = repo.get("primaryLanguage")
                lang = lang_node.get("name") if lang_node else "Unknown"
                
                print(f"⭐ {stars:,} | {name} ({lang})")
                print(f"   📅 Created: {created}")
                print(f"   📝 {desc}")
                print(f"   🔗 {repo.get('url')}\n")

if __name__ == "__main__":
    main()
