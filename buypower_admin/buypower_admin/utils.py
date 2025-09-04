import frappe
import json

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