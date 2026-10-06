# Copyright (c) 2024, Siddarth and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.core.doctype.user.user import generate_keys
from frappe.utils import get_url
from frappe.permissions import add_permission
from frappe.utils import getdate


class TSTallySettings(Document):

    def validate(doc):
        for i in doc.company_table:
            year_start_date, year_end_date = frappe.get_value(
                "Fiscal Year",
                i.fiscal_year,
                ["year_start_date", "year_end_date"]
            )

            sync_from_date = getdate(i.sync_from)

            # Compare dates
            if sync_from_date < year_start_date or sync_from_date > year_end_date:
                frappe.throw(
                    f"Row {i.idx}: Sync From date ({sync_from_date}) must be between "
                    f"{year_start_date} and {year_end_date} for Fiscal Year {i.fiscal_year}"
                )





@frappe.whitelist()
def get_not_synced_data(company):
	if not company:
		return {}

	settings = frappe.get_single("TS Tally Settings")
	company_row = None
	for row in settings.company_table:
		if row.company_name == company:
			company_row = row
			break

	if not company_row:
		frappe.throw(f"Company {company} is not configured in TS Tally Settings")

	sync_from = company_row.sync_from
	sync_to = company_row.sync_to
	sync_master_from = company_row.sync_master_from
	cost_center = company_row.cost_center
	company_number = company_row.company_number
	result = {}

	if sync_from and sync_to:
		posting_date_filter = ["between", [sync_from, sync_to]]
	elif sync_from:
		posting_date_filter = [">=", sync_from]
	else:
		posting_date_filter = ["is", "set"]

	def with_cost_center(filters):
		if cost_center:
			filters["cost_center"] = cost_center
		return filters

	# Master Data - Items
	synced_items = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Item", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_items = frappe.get_all(
		"Item",
		filters={
			"has_variants": 0,
			"name": ["not in", synced_items],
			"creation": [">=", sync_master_from] if sync_master_from else ["is", "set"]
		},
		fields=["name", "item_name"],
		order_by="creation desc"
	)
	if not_synced_items:
		result["Item"] = not_synced_items

	# Master Data - Item Group
	synced_item_groups = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Item Group", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_item_groups = frappe.get_all(
		"Item Group",
		filters={
			"name": ["not in", synced_item_groups],
			"is_group": 0
		},
		fields=["name"],
		order_by="creation desc"
	)
	if not_synced_item_groups:
		result["Item Group"] = not_synced_item_groups

	# Master Data - Warehouse
	synced_warehouses = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Warehouse", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_warehouses = frappe.get_all(
		"Warehouse",
		filters={
			"name": ["not in", synced_warehouses],
			"company": company,
			"is_group": 0
		},
		fields=["name"],
		order_by="creation desc"
	)
	if not_synced_warehouses:
		result["Warehouse"] = not_synced_warehouses

	# Master Data - Supplier
	synced_suppliers = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Supplier", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_suppliers = frappe.get_all(
		"Supplier",
		filters={
			"name": ["not in", synced_suppliers]
		},
		fields=["name", "supplier_name"],
		order_by="creation desc"
	)
	if not_synced_suppliers:
		result["Supplier"] = not_synced_suppliers

	# Master Data - Customer
	synced_customers = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Customer", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_customers = frappe.get_all(
		"Customer",
		filters={
			"name": ["not in", synced_customers],
			"creation": [">=", sync_master_from] if sync_master_from else ["is", "set"]
		},
		fields=["name", "customer_name"],
		order_by="creation desc"
	)
	if not_synced_customers:
		result["Customer"] = not_synced_customers

	# Master Data - Account
	synced_accounts = frappe.get_all(
		"Tally Master Sync Log",
		filters={"parenttype": "Account", "status": "SUCCESS", "company_number": company_number},
		pluck="parent"
	)
	not_synced_accounts = frappe.get_all(
		"Account",
		filters={
			"name": ["not in", synced_accounts],
			"company": company,
			"is_group": 0
		},
		fields=["name", "account_name"],
		order_by="creation desc"
	)
	if not_synced_accounts:
		result["Account"] = not_synced_accounts

	# Transactional Data - Sales Invoice
	not_synced_si = frappe.get_all(
		"Sales Invoice",
		filters=with_cost_center({
			"custom_tally_guid": ["is", "not set"],
			"company": company,
			"docstatus": 1,
			"is_opening": ["!=", "Yes"],
			"posting_date": posting_date_filter
		}),
		fields=["name", "customer_name", "cost_center", "posting_date", "grand_total"],
		order_by="posting_date desc"
	)
	if not_synced_si:
		result["Sales Invoice"] = not_synced_si

	# Transactional Data - Purchase Invoice
	not_synced_pi = frappe.get_all(
		"Purchase Invoice",
		filters=with_cost_center({
			"custom_tally_guid": ["is", "not set"],
			"company": company,
			"docstatus": 1,
			"is_opening": ["!=", "Yes"],
			"posting_date": posting_date_filter
		}),
		fields=["name", "supplier_name", "cost_center", "posting_date", "grand_total"],
		order_by="posting_date desc"
	)
	if not_synced_pi:
		result["Purchase Invoice"] = not_synced_pi

	# Transactional Data - Payment Entry
	not_synced_pe = frappe.get_all(
		"Payment Entry",
		filters=with_cost_center({
			"custom_tally_guid": ["is", "not set"],
			"company": company,
			"docstatus": 1,
			"posting_date": posting_date_filter
		}),
		fields=["name", "payment_type", "party_name", "cost_center", "posting_date", "paid_amount"],
		order_by="posting_date desc"
	)
	if not_synced_pe:
		result["Payment Entry"] = not_synced_pe

	# Transactional Data - Journal Entry
	je_filters = {
		"custom_tally_guid": ["is", "not set"],
		"company": company,
		"docstatus": 1,
		"is_opening": ["!=", "Yes"],
		"posting_date": posting_date_filter
	}
	if cost_center:
		je_names_with_cc = frappe.get_all(
			"Journal Entry Account",
			filters={"cost_center": cost_center, "parenttype": "Journal Entry"},
			pluck="parent",
			distinct=True
		)
		je_filters["name"] = ["in", je_names_with_cc] if je_names_with_cc else ["in", [""]]
	not_synced_je = frappe.get_all(
		"Journal Entry",
		filters=je_filters,
		fields=["name", "voucher_type", "posting_date", "total_debit"],
		order_by="posting_date desc"
	)
	if not_synced_je:
		je_cc_rows = frappe.get_all(
			"Journal Entry Account",
			filters={
				"parenttype": "Journal Entry",
				"parent": ["in", [je["name"] for je in not_synced_je]],
				"cost_center": ["is", "set"]
			},
			fields=["parent", "cost_center"]
		)
		cc_by_je = {}
		for row in je_cc_rows:
			cc_by_je.setdefault(row["parent"], []).append(row["cost_center"])
		for je in not_synced_je:
			je["cost_center"] = ", ".join(sorted(set(cc_by_je.get(je["name"], []))))
		result["Journal Entry"] = [
			{"name": je["name"], "voucher_type": je["voucher_type"], "cost_center": je["cost_center"],
			 "posting_date": je["posting_date"], "total_debit": je["total_debit"]}
			for je in not_synced_je
		]

	# Transactional Data - Stock Entry
	not_synced_se = frappe.get_all(
		"Stock Entry",
		filters={
			"custom_tally_guid": ["is", "not set"],
			"company": company,
			"docstatus": 1,
			"is_opening": ["!=", "Yes"],
			"posting_date": posting_date_filter
		},
		fields=["name", "stock_entry_type", "posting_date"],
		order_by="posting_date desc"
	)
	if not_synced_se:
		result["Stock Entry"] = not_synced_se

	# not_synced_dn = frappe.get_all(
	# 	"Delivery Note",
	# 	filters={
	# 		"custom_tally_guid": ["is", "not set"],
	# 		"company": company,
	# 		"docstatus": 1,
	# 		"posting_date": posting_date_filter
	# 	},
	# 	fields=["name", "customer_name", "posting_date", "grand_total"],
	# 	order_by="posting_date desc"
	# )
	# if not_synced_dn:
	# 	result["Delivery Note"] = not_synced_dn

	return result


@frappe.whitelist()
def get_sync_dashboard_data(from_date=None, to_date=None):
	"""
	Returns dashboard stats for the Sync Dashboard tab.

	Shape:
	{
		"global_masters": [ {doctype, pending, synced, last_sync, last_failure}, ... ],
		"companies": [
			{
				"company": "Thirvusoft",
				"sync_from": "...", "cost_center": "...",
				"masters": [ {...} ],
				"vouchers": [ {...} ]
			}, ...
		]
	}
	"""
	settings = frappe.get_single("TS Tally Settings")
	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None

	def date_window(lo, hi):
		"""The company's own sync window narrowed by the dashboard's dates."""
		lo = getdate(lo) if lo else None
		hi = getdate(hi) if hi else None
		if from_date and (not lo or from_date > lo):
			lo = from_date
		if to_date and (not hi or to_date < hi):
			hi = to_date
		return lo, hi

	def date_filter(field, lo, hi):
		if lo and hi:
			return {field: ["between", [lo, hi]]}
		if lo:
			return {field: [">=", lo]}
		if hi:
			return {field: ["<=", hi]}
		return {}

	def master_stats(parent_doctype, base_filters=None, company_number=None):
		# Masters have no posting date: the dashboard dates count the records
		# created in that period.
		base_filters = dict(base_filters or {})
		# "between" on a datetime runs to the end of the To day.
		if from_date or to_date:
			base_filters["creation"] = ["between", [from_date or "2000-01-01", to_date or "2999-12-31"]]
		log_filters = {"parenttype": parent_doctype, "status": "SUCCESS"}
		if company_number is not None:
			log_filters["company_number"] = company_number

		synced_names = frappe.get_all(
			"Tally Master Sync Log",
			filters=log_filters,
			pluck="parent",
			distinct=True,
		)
		pending_filters = dict(base_filters)
		pending_filters["name"] = ["not in", synced_names or [""]]
		pending = frappe.db.count(parent_doctype, filters=pending_filters)

		synced_filters = dict(base_filters)
		synced_filters["name"] = ["in", synced_names] if synced_names else ["in", [""]]
		synced = frappe.db.count(parent_doctype, filters=synced_filters) if synced_names else 0

		if company_number is not None:
			last_sync = frappe.db.sql(
				"""
				select max(sync_time) from `tabTally Master Sync Log`
				where parenttype=%s and status='SUCCESS' and company_number=%s
				""",
				(parent_doctype, company_number),
			)
		else:
			last_sync = frappe.db.sql(
				"""
				select max(sync_time) from `tabTally Master Sync Log`
				where parenttype=%s and status='SUCCESS'
				""",
				(parent_doctype,),
			)
		last_sync = last_sync[0][0] if last_sync and last_sync[0] else None

		return {
			"doctype": parent_doctype,
			"pending": pending,
			"synced": synced,
			"last_sync": last_sync,
		}

	def voucher_stats(doctype, company, sync_from, cost_center, extra_pending_filters=None,
					  cost_center_in_child=False, sync_to=None, label=None):
		base = {"company": company, "docstatus": 1}
		if extra_pending_filters:
			base.update(extra_pending_filters)

		pending_filters = dict(base)
		pending_filters["custom_tally_guid"] = ["is", "not set"]
		# Pending: inside the company's sync window, narrowed by the dashboard dates.
		pending_filters.update(date_filter("posting_date", *date_window(sync_from, sync_to)))
		if cost_center and not cost_center_in_child:
			pending_filters["cost_center"] = cost_center

		synced_filters = dict(base)
		synced_filters["custom_tally_guid"] = ["is", "set"]
		# Synced: whatever was posted in the dashboard dates, sync window or not.
		synced_filters.update(date_filter("posting_date", from_date, to_date))
		if cost_center and not cost_center_in_child:
			synced_filters["cost_center"] = cost_center

		if cost_center and cost_center_in_child:
			# Journal Entry case — cost_center lives on Journal Entry Account
			matching = frappe.get_all(
				"Journal Entry Account",
				filters={"parenttype": doctype, "cost_center": cost_center},
				pluck="parent",
				distinct=True,
			) or [""]
			pending_filters["name"] = ["in", matching]
			synced_filters["name"] = ["in", matching]

		pending = frappe.db.count(doctype, filters=pending_filters)
		synced = frappe.db.count(doctype, filters=synced_filters)

		last_sync_row = frappe.get_all(
			doctype,
			filters=synced_filters,
			fields=["modified"],
			order_by="modified desc",
			limit=1,
		)
		last_sync = last_sync_row[0]["modified"] if last_sync_row else None

		return {
			"doctype": label or doctype,
			"pending": pending,
			"synced": synced,
			"last_sync": last_sync,
		}

	global_masters = []

	companies = []
	for row in settings.company_table:
		company = row.company_name
		company_number = row.company_number
		sync_from = row.sync_from
		sync_to = row.sync_to
		cost_center = row.cost_center

		masters = [
			master_stats("Item", {"has_variants": 0}, company_number=company_number),
			master_stats("Item Group", {"is_group": 0}, company_number=company_number),
			master_stats("Customer", company_number=company_number),
			master_stats("Supplier", company_number=company_number),
			master_stats("Account", {"company": company, "is_group": 0}, company_number=company_number),
			master_stats("Warehouse", {"company": company, "is_group": 0}, company_number=company_number),
		]

		vouchers = [
			voucher_stats("Sales Invoice", company, sync_from, cost_center,
						  {"is_opening": ["!=", "Yes"], "is_return": 0}, sync_to=sync_to),
			voucher_stats("Sales Invoice", company, sync_from, cost_center,
						  {"is_return": 1}, sync_to=sync_to, label="Credit Note"),
			voucher_stats("Purchase Invoice", company, sync_from, cost_center,
						  {"is_opening": ["!=", "Yes"], "is_return": 0}, sync_to=sync_to),
			voucher_stats("Purchase Invoice", company, sync_from, cost_center,
						  {"is_return": 1}, sync_to=sync_to, label="Debit Note"),
			voucher_stats("Payment Entry", company, sync_from, cost_center, sync_to=sync_to),
			voucher_stats("Journal Entry", company, sync_from, cost_center,
						  {"is_opening": ["!=", "Yes"]}, cost_center_in_child=True, sync_to=sync_to),
			voucher_stats("Stock Entry", company, sync_from, None,
						  {"is_opening": ["!=", "Yes"]}, sync_to=sync_to),
		]

		companies.append({
			"company": company,
			"sync_from": sync_from,
			"cost_center": cost_center,
			"masters": masters,
			"vouchers": vouchers,
		})

	return {
		"global_masters": global_masters,
		"companies": companies,
		"from_date": from_date,
		"to_date": to_date,
	}


@frappe.whitelist()
def get_unmapped_accounts():
	unmapped_account = frappe.db.get_list('Account', filters={'custom_tally_parent_account':['=',''], 'is_group':0}, fields=['name', 'company'])
	return unmapped_account


def user_creation(user_id):

    if frappe.db.exists("User",user_id):
        return
    user = frappe.new_doc("User")
    user.email = user_id
    user.first_name = "Tally"
    user.last_name = "User"
    user.send_welcome_email = 0
    user.username = "tallyuser"
    user.add_roles("Tally User")
    user.save()
    api_generate_secret = generate_keys(user_id)

    secret_key = api_generate_secret["api_secret"]
    api_key = frappe.db.get_value("User", user_id, "api_key")

    base_url = get_url()

    item_master = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.item.get_item" # Item
    item_group = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.item_group.get_itemgroup" # Itemgroup
    warehouse = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.warehouse.get_warehouse" # Warehouse
    party = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.party.get_party" # Party


    purchase_invoice_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.purchase_invoice_inventory.get_purchase_invoice" # Purchase Invoice (Inventory)
    purchase_invoice_non_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.purchase_invoice_non_inventory.get_purchase_invoice" # Purchase Invoice (Non-Inventory)

    debit_note_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.debit_note_inventory.get_debit_note" # Purchase Invoice (Return)
    debit_note_non_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.debit_note_non_inventory.get_debit_note" # Purchase Invoice Return (Non-Inventory) 

    payment_entry_pay = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.payment_entry_pay.get_payment_entry_pay" # Payment Entry type (Pay)
    payment_entry_receipt = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.payment_entry_receipt.get_payment_entry_receipt" # Payment Entry type (Receive)

    sales_invoice_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.sales_invoice_inventory.get_sales_inv" # Sales Invocie (Inventory)
    sales_invoice_non_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.sales_invoice_non_inventory.get_sales_non_inv" # Sales Invocie (Non-Inventory)

    credit_note_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.credit_note_inventory.credit_note_inv" # Credit Note (Inventory)
    credit_note_non_inventory = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.credit_note_non_inventory.credit_note_non_inv"  # Credit Note (Non-Inventory)

    journal_entry = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.journal_entry.get_journal"  # journal Entry
    contra_entry = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.contra_entry.get_contra" # Contra Entry

    api_details = f'''API Key: {api_key}

    Secret Key: {secret_key}

    Item Master: {item_master}

    Item Group: {item_group}

    Warehouse: {warehouse}

    Party: {party}

    Purchase Invoice (Inventory): {purchase_invoice_inventory}

    Purchase Invoice (Non-Inventory): {purchase_invoice_non_inventory}

    Debit Note (Inventory): {debit_note_inventory}

    Debit Note (Non-Inventory): {debit_note_non_inventory}

    Sales Invoice (Inventory): {sales_invoice_inventory}

    Sales Invoice (Non-Inventory): {sales_invoice_non_inventory}

    Credit Note (Inventory): {credit_note_inventory}

    Credit Note (Non-Inventory): {credit_note_non_inventory}

    Journal Entry: {journal_entry}

    Contra Entry: {contra_entry}

    Payment Entry (Pay): {payment_entry_pay}

    Payment Entry (Receipt): {payment_entry_receipt}'''

    frappe.db.set_value("TS Tally Settings", "TS Tally Settings", "api_details", api_details)
    set_response_url(base_url)


def role_creation(role_name):
    if frappe.db.exists("Role", role_name):
        return
    role = frappe.new_doc("Role")
    role.role_name = role_name
    role.desk_access = 0
    role.save()


def role_permission(role_name):
    doctypes = [
         "GL Entry",
         "Journal Entry",
         "Address",
         "Customer",
         "Account",
         "Sales Invoice",
         "Purchase Invoice",
         "Delivery Note",
         "Purchase Receipt",
         "Stock Entry",
         "Payment Entry",
         "Company",
         "Account",
         "Supplier",
         "Customer",
         "Item",
         "Item Group",
         "Warehouse",
         "Item Tax Template",
         "Fiscal Year"
         ]
    
    for doctype in doctypes:
        existing_permission = frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role_name})
        
        if not existing_permission:
            add_permission(doctype, role_name, 0, "read")


def set_response_url(base_url):

    item_master_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.item.fetch_response" # Item
    item_group_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.item_group.fetch_response" # Itemgroup
    warehouse_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.warehouse.fetch_response" # Warehouse
    party_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.party.fetch_response" # Party


    purchase_invoice_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.purchase_invoice_inventory.fetch_response" # Purchase Invoice (Inventory)
    purchase_invoice_non_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.purchase_invoice_non_inventory.fetch_response" # Purchase Invoice (Non-Inventory)

    debit_note_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.debit_note_inventory.fetch_response" # Purchase Invoice (Return)
    debit_note_non_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.debit_note_non_inventory.fetch_response" # Purchase Invoice Return (Non-Inventory) 

    payment_entry_pay_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.payment_entry_pay.fetch_response" # Payment Entry type (Pay)
    payment_entry_receipt_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.payment_entry_receipt.fetch_response" # Payment Entry type (Receive)

    sales_invoice_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.sales_invoice_inventory.fetch_response" # Sales Invocie (Inventory)
    sales_invoice_non_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.sales_invoice_non_inventory.fetch_response" # Sales Invocie (Non-Inventory)

    credit_note_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.credit_note_inventory.fetch_response" # Credit Note (Inventory)
    credit_note_non_inventory_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.credit_note_non_inventory.fetch_response"  # Credit Note (Non-Inventory)

    journal_entry_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.journal_entry.fetch_response"  # journal Entry
    contra_entry_response = f"{base_url}/api/method/ts_tally_integration.tally_integration.utils.api.contra_entry.fetch_response" # Contra Entry

    response_api_details = f'''

    Item Master: {item_master_response}

    Item Group: {item_group_response}

    Warehouse: {warehouse_response}

    Party: {party_response}

    Purchase Invoice (Inventory): {purchase_invoice_inventory_response}

    Purchase Invoice (Non-Inventory): {purchase_invoice_non_inventory_response}

    Debit Note (Inventory): {debit_note_inventory_response}

    Debit Note (Non-Inventory): {debit_note_non_inventory_response}

    Sales Invoice (Inventory): {sales_invoice_inventory_response}

    Sales Invoice (Non-Inventory): {sales_invoice_non_inventory_response}

    Credit Note (Inventory): {credit_note_inventory_response}

    Credit Note (Non-Inventory): {credit_note_non_inventory_response}

    Journal Entry: {journal_entry_response}

    Contra Entry: {contra_entry_response}

    Payment Entry (Pay): {payment_entry_pay_response}

    Payment Entry (Receipt): {payment_entry_receipt_response}'''

    frappe.db.set_value("TS Tally Settings", "TS Tally Settings", "response_api_details", response_api_details)

