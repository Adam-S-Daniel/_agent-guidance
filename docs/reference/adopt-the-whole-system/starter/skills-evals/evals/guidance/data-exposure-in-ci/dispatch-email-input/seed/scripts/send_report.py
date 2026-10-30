"""Send the weekly report to one recipient."""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="recipient email address")
    args = parser.parse_args()
    print(f"Sending report to {args.to}")
    # The mailer is wired up in production; this stub only logs.


if __name__ == "__main__":
    main()
