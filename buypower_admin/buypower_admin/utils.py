import frappe
import json
import hmac
import hashlib
import requests

def safe_log_error(message, title="Log"):
    """Safely log errors with proper title length limits"""
    # Ensure title doesn't exceed 130 characters
    if len(title) > 130:
        title = title[:127] + "..."
    
    # Ensure message doesn't exceed reasonable limits
    if len(str(message)) > 3000:
        message = str(message)[:3000] + "... (truncated)"
    
    try:
        frappe.log_error(message=str(message), title=title)
    except Exception:
        # If logging still fails, use print as fallback
        print(f"Log failed: {title} - {str(message)[:100]}")





def _verify_webhook_signature(raw_body):
    """
    Verify a BuyPower MFB webhook signature (HMAC-SHA256 hex over the raw body).

    If a signature header is present it MUST validate against
    `buypower_webhook_secret`; if absent, the call is treated as trusted.
    """
    try:
        headers = getattr(frappe.request, "headers", {}) or {}
        signature = headers.get("x-buypower-signature") or headers.get("x-panbox-signature")
    except Exception:
        signature = None

    if not signature:
        return True

    secret = frappe.conf.get("buypower_webhook_secret")
    if not secret:
        # No secret configured — cannot verify. Allow through and warn.
        frappe.logger().warning(
            "Webhook received with signature header but buypower_webhook_secret is not configured — "
            "treating as trusted. Set buypower_webhook_secret in site_config.json to enforce verification."
        )
        return True

    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def _forward_to_site(site_name, payload):
    """Forward the webhook payload to a client site's wallet_log endpoint."""
    if not site_name:
        return
    url = f"https://{site_name}/api/method/purpledove_payment.utils.wallet_log"
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as post_error:
        frappe.log_error(title="Wallet Forwarding Error", message=f"Failed to POST to {url}: {str(post_error)}")


@frappe.whitelist(allow_guest=True)
def wallet_log():
    """
    Central BuyPower MFB webhook receiver.

    Verifies the signature, logs the event, and forwards it to the originating
    client site. Handles v2 `{type, data}` and legacy `{event, data}`:
      - static_account.transaction.created / invoice.paid -> inflow
      - transfer.pending | transfer.paid | transfer.failed -> outflow
    """
    try:
        raw = frappe.request.get_data()  # raw bytes for signature verification

        if not _verify_webhook_signature(raw):
            frappe.local.response["http_status_code"] = 401
            return {"success": False, "error": "Invalid webhook signature"}

        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

        # v2 uses "type"; legacy uses "event"
        event = payload.get("type") or payload.get("event")
        data = payload.get("data", {}) or {}

        # Normalize nested BuyPower fields (with legacy fallbacks)
        source = data.get("source", {}) or {}
        destination = data.get("destination", {}) or {}
        amount_obj = data.get("amount", {})
        amount = float(amount_obj.get("value", 0)) if isinstance(amount_obj, dict) else float(amount_obj or 0)
        metadata = data.get("metadata", {}) or {}

        is_inflow = event in ("static_account.transaction.created", "invoice.paid")
        is_transfer = event in ("transfer.pending", "transfer.paid", "transfer.failed")

        # Map status -> log Select options (CONFIRMED / PENDING / FAILED)
        raw_status = (data.get("status") or (event.split(".")[-1] if event else "")).lower()
        status_map = {
            "paid": "CONFIRMED", "successful": "CONFIRMED", "success": "CONFIRMED",
            "pending": "PENDING", "processing": "PENDING", "failed": "FAILED",
        }
        log_status = status_map.get(raw_status, "PENDING")
        transaction_type = "INFLOW" if is_inflow else ("OUTFLOW" if is_transfer else None)

        # Our reserved account: for an inflow it is the destination; for a
        # transfer the source wallet (destination is the external recipient).
        if is_inflow:
            our_account = destination.get("accountNumber")
        else:
            our_account = source.get("accountNumber") or metadata.get("source_account_number")
        our_account = our_account or data.get("accountNumber")

        # Insert admin log (doctype name has a double space, kept as-is)
        wallet_log_doc = frappe.get_doc({
            "doctype": "Purpledove Admin  Log",
            "event": event,
            "transaction_reference": data.get("reference") or data.get("transactionReference"),
            "session_id": data.get("sessionId"),
            "account_number": our_account,
            "account_type": data.get("type") or data.get("accountType"),
            "amount": amount,
            "source_account_name": source.get("accountName") or data.get("sourceAccountName"),
            "source_account_number": source.get("accountNumber") or data.get("sourceAccountNumber"),
            "source_bank_name": source.get("bankName") or data.get("sourceBankName"),
            "source_bank_code": source.get("bankCode") or data.get("sourceBankCode"),
            "destination_account_number": destination.get("accountNumber") or data.get("destinationAccountNumber"),
            "destination_account_name": destination.get("accountName") or data.get("destinationAccountName"),
            "destination_bank_name": destination.get("bankName") or data.get("destinationBankName"),
            "destination_bank_code": destination.get("bankCode") or data.get("destinationBankCode"),
            "transaction_type": transaction_type,
            "status": log_status,
            "narration": data.get("narration"),
            "metadata": json.dumps(metadata),
            "data_details": json.dumps(payload),
        })
        wallet_log_doc.insert(ignore_permissions=True)

        # Resolve the destination site for forwarding.
        # Transfers carry the originating site in metadata; inflows are matched
        # to a Client Wallet by the credited account number.
        client_wallet_info = None
        site_name = metadata.get("site") if is_transfer else None
        if not site_name and our_account:
            wallet_list = frappe.get_all(
                "Client Wallet",
                filters={"account_number": our_account},
                fields=["name", "wallet_name", "account_number", "site_name", "wallet_status"],
                limit=1,
            )
            if wallet_list:
                cw = wallet_list[0]
                site_name = cw.site_name
                client_wallet_info = {
                    "name": cw.name,
                    "wallet_name": cw.wallet_name,
                    "account_number": cw.account_number,
                    "site_name": cw.site_name,
                    "wallet_status": cw.wallet_status,
                }
            elif is_inflow:
                frappe.log_error(
                    title="Inflow Webhook Not Forwarded",
                    message=f"No Client Wallet found for account_number={our_account!r}. Event '{event}' dropped."
                )

        _forward_to_site(site_name, payload)
        frappe.db.commit()

        return {
            "success": True,
            "message": "Data logged successfully",
            "forwarded_to": site_name,
            "client_wallet": client_wallet_info,
        }

    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON received"}
    except Exception as e:
        frappe.log_error(title="Wallet Log Error", message=str(e))
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def client_wallet():
    """Handle wallet creation requests from client systems"""
    try:
        # Get the raw request for debugging - FIXED LINE 103
        raw_data = frappe.request.get_data(as_text=True)
        safe_log_error(f"Raw data: {raw_data[:200]}", "Client Req")
        
        # Get the incoming request data - handle multiple formats
        payload = None
        
        # Try JSON first
        try:
            payload = json.loads(raw_data)
            safe_log_error("Successfully parsed as JSON", "Req Format")
        except json.JSONDecodeError:
            # Try form data
            form_data = frappe.form_dict
            safe_log_error(f"Form data: {dict(form_data)}", "Form Data")
            
            if form_data.get('event') and form_data.get('data'):
                try:
                    data_value = form_data.get('data')
                    if isinstance(data_value, str):
                        parsed_data = json.loads(data_value)
                    else:
                        parsed_data = data_value
                    
                    payload = {
                        'event': form_data.get('event'),
                        'data': parsed_data
                    }
                    safe_log_error("Successfully parsed form data", "Req Format")
                except json.JSONDecodeError as e:
                    safe_log_error(f"Form JSON error: {str(e)[:50]}", "Form Err")
                    return {"success": False, "error": "Invalid JSON in form data"}
            else:
                safe_log_error("No valid event/data in form", "Form Err")
                return {"success": False, "error": "Invalid request format - no event/data found"}

        if not payload:
            return {"success": False, "error": "Could not parse request data"}

        # Log the parsed payload safely
        log_payload = payload.copy()
        if log_payload.get("data", {}).get("bvn"):
            log_payload["data"]["bvn"] = "***masked***"
        safe_log_error(f"Payload: {json.dumps(log_payload, indent=2)[:300]}", "Parsed Payload")

        # Extract the "event" and "data" fields from the payload
        event = payload.get("event")
        transaction_data = payload.get("data", {})
        
        # Validate event type
        if event != "wallet_created":
            return {"success": False, "error": f"Invalid event type. Expected 'wallet_created', got '{event}'"}
        
        # Check if required wallet data is present
        wallet_name = transaction_data.get("wallet_name")
        if not wallet_name:
            return {"success": False, "error": "wallet_name is required"}
        
        # Get site_name from transaction data, with fallback
        site_name = transaction_data.get("site_name", "")
        if not site_name:
            return {"success": False, "error": "site_name is required"}
        
        # Handle BVN validation more gracefully
        bvn = transaction_data.get("bvn")
        bvn_to_save = None
        bvn_warning = None
        
        if bvn:
            # Remove any spaces or special characters
            bvn_clean = ''.join(filter(str.isdigit, str(bvn)))
            
            # Check if BVN is exactly 11 digits
            if len(bvn_clean) == 11:
                bvn_to_save = bvn_clean
                safe_log_error(f"Valid BVN for wallet: {wallet_name}", "BVN Valid")
            else:
                # Option 1: Skip BVN and continue (more graceful)
                bvn_warning = f"Invalid BVN provided ({len(bvn_clean)} digits), wallet created without BVN"
                safe_log_error(bvn_warning, "BVN Warning")
                
                # Option 2: Return error (stricter approach)
                # return {"success": False, "error": "BVN must be exactly 11 digits"}
        
        safe_log_error(f"Processing wallet: {wallet_name} for site: {site_name}", "Processing")
        
        # Check if a wallet with the same name already exists for this site
        existing_wallet = frappe.db.exists("Client Wallet", {
            "wallet_name": wallet_name,
            "site_name": site_name
        })
        
        if existing_wallet:
            # Get existing wallet details for logging
            existing_doc = frappe.get_doc("Client Wallet", existing_wallet)
            safe_log_error(f"Replacing existing wallet: {wallet_name}", "Replacing")
            
            # Delete the existing record
            frappe.delete_doc("Client Wallet", existing_wallet, ignore_permissions=True)
            frappe.db.commit()
        
        # Create a new record in the Client Wallet Doctype
        wallet_data = {
            "doctype": "Client Wallet",
            "site_name": site_name,
            "wallet_name": wallet_name,
            "currency": transaction_data.get("currency", "NGN"),
            "wallet_id": transaction_data.get("wallet_id"),
            "description": transaction_data.get("description"),
            "account_number": transaction_data.get("account_number"),
            "exchange_ref": transaction_data.get("exchange_ref"),
            "business_id": transaction_data.get("business_id"),
            "account_type": transaction_data.get("account_type", "Wallet"),
            "bank_code": transaction_data.get("bank_code"),
            "bank_name": transaction_data.get("bank_name")
        }
        
        # Only add BVN if it's valid
        if bvn_to_save:
            wallet_data["bvn"] = bvn_to_save
        
        client_wallet_doc = frappe.get_doc(wallet_data)
        
        # Save the document
        client_wallet_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Log successful creation
        safe_log_error(f"Successfully created Client Wallet: {wallet_name}", "Success")

        response = {
            "success": True, 
            "message": "Wallet created successfully",
            "wallet_data": client_wallet_doc.as_dict()
        }
        
        # Add warning if BVN was invalid
        if bvn_warning:
            response["warning"] = bvn_warning
        
        return response

    except json.JSONDecodeError as e:
        safe_log_error(f"JSON decode error: {str(e)}", "JSON Error")
        return {"success": False, "error": "Invalid JSON payload"}
    except frappe.ValidationError as e:
        # Handle Frappe validation errors specifically - FIXED LINE 249
        error_msg = str(e)
        safe_log_error(f"Validation error: {error_msg[:100]}", "Val Error")
        return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = str(e)
        safe_log_error(f"Error: {error_msg[:100]}", "Creation Error")
        return {"success": False, "error": error_msg}