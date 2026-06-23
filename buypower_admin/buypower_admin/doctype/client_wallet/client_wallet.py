# Copyright (c) 2025, Lassod Consulting Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

class ClientWallet(Document):
	def before_insert(self):
		"""Handle wallet sequence and validation before inserting"""
		
		# Auto-generate wallet sequence for the site
		existing_wallets = frappe.get_all("Client Wallet", 
										filters={"site_name": self.site_name}, 
										fields=["wallet_sequence"],
										order_by="wallet_sequence desc",
										limit=1)
		
		if existing_wallets:
			self.wallet_sequence = existing_wallets[0].wallet_sequence + 1
		else:
			self.wallet_sequence = 1
		
		# Set created_by_user
		self.created_by_user = frappe.session.user
		
		# Generate unique wallet_id if not set
		if not self.wallet_id:
			self.wallet_id = f"WLT-{self.site_name}-{self.wallet_sequence:05d}"

	def before_save(self):
		"""Validate wallet constraints before saving"""
		
		# Ensure only one primary wallet per site
		if self.is_primary_wallet:
			existing_primary = frappe.get_all("Client Wallet", 
											filters={
												"site_name": self.site_name, 
												"is_primary_wallet": 1,
												"name": ["!=", self.name]
											})
			if existing_primary:
				frappe.throw(_("A primary wallet already exists for site {0}. Please uncheck 'Is Primary Wallet' or update the existing primary wallet.").format(self.site_name))
		
		# Validate BVN format if provided
		if self.bvn and len(self.bvn) != 11:
			frappe.throw(_("BVN must be exactly 11 digits"))
		
		# Auto-set first wallet as primary if no primary exists
		if not self.is_primary_wallet:
			existing_wallets = frappe.get_all("Client Wallet", 
											filters={"site_name": self.site_name})
			if len(existing_wallets) == 0:  # This is the first wallet
				self.is_primary_wallet = 1

	def validate(self):
		"""Additional validation rules"""
		
		# Validate wallet name uniqueness per site
		existing_wallet = frappe.get_all("Client Wallet", 
									   filters={
										   "site_name": self.site_name,
										   "wallet_name": self.wallet_name,
										   "name": ["!=", self.name]
									   })
		if existing_wallet:
			frappe.throw(_("Wallet name '{0}' already exists for site '{1}'").format(self.wallet_name, self.site_name))
	
	def get_wallet_balance(self):
		"""Get current wallet balance - implement based on your transaction logic"""
		# This would typically query your transaction records
		# Example implementation:
		balance = frappe.db.sql("""
			SELECT 
				COALESCE(SUM(CASE WHEN transaction_type = 'Credit' THEN amount ELSE -amount END), 0) as balance
			FROM `tabWallet Transaction`
			WHERE wallet_id = %s AND docstatus = 1
		""", (self.wallet_id,))
		
		return balance[0][0] if balance else 0.0
	
	def create_transaction(self, transaction_type, amount, description="", reference=None):
		"""Create a new wallet transaction"""
		transaction = frappe.new_doc("Wallet Transaction")
		transaction.wallet_id = self.wallet_id
		transaction.site_name = self.site_name
		transaction.transaction_type = transaction_type
		transaction.amount = amount
		transaction.description = description
		transaction.reference = reference
		transaction.insert()
		transaction.submit()
		
		return transaction

# Module-level utility functions
@frappe.whitelist()
def get_wallets_by_site(site_name, status=None):
	"""Get all wallets for a specific site"""
	filters = {"site_name": site_name}
	if status:
		filters["wallet_status"] = status
	
	wallets = frappe.get_all("Client Wallet",
						   filters=filters,
						   fields=["name", "wallet_name", "wallet_id", "currency", 
								  "wallet_status", "is_primary_wallet", "account_number",
								  "wallet_sequence"],
						   order_by="wallet_sequence asc")
	
	return wallets

@frappe.whitelist()
def create_bulk_wallets(site_name, wallet_data):
	"""Create multiple wallets for a site at once"""
	import json
	
	if isinstance(wallet_data, str):
		wallet_data = json.loads(wallet_data)
	
	created_wallets = []
	
	for wallet_info in wallet_data:
		wallet_doc = frappe.new_doc("Client Wallet")
		wallet_doc.site_name = site_name
		wallet_doc.wallet_name = wallet_info.get("wallet_name")
		wallet_doc.currency = wallet_info.get("currency", "NGN")
		wallet_doc.description = wallet_info.get("description", "")
		wallet_doc.bvn = wallet_info.get("bvn")
		
		try:
			wallet_doc.insert()
			created_wallets.append({
				"name": wallet_doc.name,
				"wallet_name": wallet_doc.wallet_name,
				"wallet_id": wallet_doc.wallet_id,
				"status": "success"
			})
		except Exception as e:
			created_wallets.append({
				"wallet_name": wallet_info.get("wallet_name"),
				"status": "error",
				"error": str(e)
			})
	
	return created_wallets

@frappe.whitelist()
def get_primary_wallet(site_name):
	"""Get the primary wallet for a site"""
	primary_wallet = frappe.get_value("Client Wallet", 
									{"site_name": site_name, "is_primary_wallet": 1},
									["name", "wallet_name", "wallet_id", "currency", "wallet_status"])
	
	if primary_wallet:
		return {
			"name": primary_wallet[0],
			"wallet_name": primary_wallet[1],
			"wallet_id": primary_wallet[2],
			"currency": primary_wallet[3],
			"wallet_status": primary_wallet[4]
		}
	return None

@frappe.whitelist()
def set_primary_wallet(wallet_name, site_name):
	"""Set a wallet as the primary wallet for a site"""
	# First, remove primary flag from all wallets in the site
	frappe.db.sql("""
		UPDATE `tabClient Wallet` 
		SET is_primary_wallet = 0 
		WHERE site_name = %s
	""", (site_name,))
	
	# Set the new primary wallet
	frappe.db.set_value("Client Wallet", wallet_name, "is_primary_wallet", 1)
	frappe.db.commit()
	
	return {"status": "success", "message": f"Primary wallet updated for site {site_name}"}