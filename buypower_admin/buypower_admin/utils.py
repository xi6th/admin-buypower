# File: client_wallet.py
# This file contains the admin endpoint code that receives the
# wallet data from the client system and creates a Client Wallet record.

import json
import frappe
import requests

@frappe.whitelist(allow_guest=True)
def client_wallet():
    try:
        # Get the incoming request data
        data = frappe.request.get_data(as_text=True)
        payload = json.loads(data)

        # Extract the "event" and "data" fields from the payload
        event = payload.get("event")
        transaction_data = payload.get("data", {})

        # Create a new record in the Client Wallet Doctype
        client_wallet_doc = frappe.get_doc({
            "doctype": "Client Wallet",
            "wallet_name": transaction_data.get("wallet_name"),
            "currency": transaction_data.get("currency"),
            "wallet_id": transaction_data.get("wallet_id"),
            "description": transaction_data.get("description"),
            "bvn": transaction_data.get("bvn"),
            "account_number": transaction_data.get("account_number"),
            "exchange_ref": transaction_data.get("exchange_ref"),
            "business_id": transaction_data.get("business_id"),
            "account_type": transaction_data.get("account_type"),
            "bank_code": transaction_data.get("bank_code"),
            "bank_name": transaction_data.get("bank_name"),
            "site_name": transaction_data.get("site_name")
        })
        client_wallet_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {"success": True, "message": "Wallet created successfully"}

    except Exception as e:
        frappe.log_error(title="Wallet Log Error", message=str(e))
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def wallet_log():
    try:
        # Get the incoming request data
        data = frappe.request.get_data(as_text=True)
        payload = json.loads(data)

        # Extract the "event" and "data" fields
        event = payload.get("event")
        transaction_data = payload.get("data", {})

        # Retrieve the account number from the transaction data
        account_number = transaction_data.get("accountNumber")
        if not account_number:
            return {"success": False, "error": "Account number is missing in transaction data."}

        # Check if a Client Wallet exists with the provided account number
        if not frappe.db.exists("Client Wallet", {"account_number": account_number}):
            return {"success": False, "error": f"Client Wallet with account number {account_number} does not exist."}

        # Insert data into Client Wallet Log Doctype
        wallet_log_doc = frappe.get_doc({
            "doctype": "Client Wallet Log",
            "event": event,
            "transaction_id": transaction_data.get("transactionId"),
            "transaction_reference": transaction_data.get("transactionReference"),
            "account_exchange_reference": transaction_data.get("accountExchangeReference"),
            "session_id": transaction_data.get("sessionId"),
            "account_number": account_number,
            "account_type": transaction_data.get("accountType"),
            "amount": transaction_data.get("amount"),
            "source_account_name": transaction_data.get("sourceAccountName"),
            "source_account_number": transaction_data.get("sourceAccountNumber"),
            "source_bank_name": transaction_data.get("sourceBankName"),
            "source_bank_code": transaction_data.get("sourceBankCode"),
            "destination_account_number": transaction_data.get("destinationAccountNumber"),
            "destination_account_name": transaction_data.get("destinationAccountName"),
            "destination_bank_name": transaction_data.get("destinationBankName"),
            "destination_bank_code": transaction_data.get("destinationBankCode"),
            "transaction_type": transaction_data.get("type"),
            "status": transaction_data.get("status"),
            "narration": transaction_data.get("narration"),
            "metadata": json.dumps(transaction_data.get("metadata", {}))  # Store metadata as a JSON string
        })
        wallet_log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Convert the document to a dictionary for further use
        wallet_log_data = wallet_log_doc.as_dict()
        
        # Fetch the Client Wallet to get the site name
        client_wallet_doc = frappe.get_doc("Client Wallet", {"account_number": account_number})
        site_name = client_wallet_doc.get("site_name")
        
        # Build the admin URL using the fetched site_name
        post_url_admin = f"https://{site_name}/api/method/virtual_payment.virtual_payment.utils.wallet_log"
        admin_payload = {
            "event": "wallet_created",
            "data": wallet_log_data
        }
        admin_headers = {"Content-Type": "application/json"}
        admin_response = requests.post(post_url_admin, headers=admin_headers, json=admin_payload)

        # Accept both 200 and 201 as success codes
        if admin_response.status_code in [200, 201]:
            admin_response_data = admin_response.json().get("message", {})
            if not admin_response_data.get("success"):
                frappe.log_error("Admin API response did not indicate success.", "Admin API Error")
                return {"success": False, "error": "Unexpected response from Admin API."}
            return {
                "success": True,
                "info": "Bank data saved successfully",
                "wallet_response": wallet_log_data,
                "admin_response": admin_response_data
            }
        else:
            error_message = (
                f"Failed to POST data to Admin API. Status Code: {admin_response.status_code}, "
                f"Response: {admin_response.text[:140]}"
            )
            frappe.log_error(error_message, "Admin API POST Error")
            return {"success": False, "error": error_message}
    except Exception as e:
        frappe.log_error(title="Wallet Log Error", message=str(e))
        return {"success": False, "error": str(e)}
