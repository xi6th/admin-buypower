import frappe
import json
import requests

def resend_webhooks_to_clients():
    """Resend all Purpledove Admin Log entries to their respective client sites."""

    # Get all logs that have a transaction_reference
    logs = frappe.get_all(
        "Purpledove Admin  Log",
        fields=["name", "transaction_reference", "event", "data_details", "account_number"],
        filters={"transaction_reference": ["is", "set"]},
        limit=500  # Process in batches
    )

    print(f"Found {len(logs)} logs to process")

    success_count = 0
    error_count = 0
    skipped_count = 0

    for log in logs:
        try:
            # Parse the data_details to get the original payload
            payload = json.loads(log.data_details) if log.data_details else {}

            if not payload:
                print(f"Skipping {log.name}: No payload data")
                skipped_count += 1
                continue

            # Get the account_number from the log
            account_number = log.account_number

            if not account_number:
                print(f"Skipping {log.name}: No account_number")
                skipped_count += 1
                continue

            # Find the client wallet for this account
            wallet = frappe.get_all(
                "Client Wallet",
                filters={"account_number": account_number},
                fields=["site_name", "wallet_name"],
                limit=1
            )

            if not wallet:
                print(f"Skipping {log.name}: No wallet found for account {account_number}")
                skipped_count += 1
                continue

            site_name = wallet[0].site_name
            wallet_name = wallet[0].wallet_name

            # Forward to client site
            url = f"https://{site_name}/api/method/purpledove_payment.utils.wallet_log"

            try:
                response = requests.post(url, json=payload, timeout=10)
                if response.status_code == 200:
                    print(f"✓ Sent {log.name} to {site_name} ({wallet_name})")
                    success_count += 1
                else:
                    print(f"✗ Failed {log.name} to {site_name}: {response.status_code}")
                    error_count += 1
            except Exception as e:
                print(f"✗ Error sending {log.name} to {site_name}: {str(e)[:50]}")
                error_count += 1

        except Exception as e:
            print(f"✗ Error processing {log.name}: {str(e)[:50]}")
            error_count += 1

    print(f"\nSummary: {success_count} successful, {error_count} errors, {skipped_count} skipped")
    return {"success": success_count, "errors": error_count, "skipped": skipped_count}

@frappe.whitelist()
def resend_webhooks():
    """Whitelisted function to resend webhooks to client sites."""
    return resend_webhooks_to_clients()