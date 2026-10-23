import frappe
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_master_sync_limit
import json
from datetime import datetime
from werkzeug.wrappers import Response
from frappe.utils import get_datetime


def is_sync_enabled(voucher_name):
    return frappe.get_value('Voucher Sync Control', {'voucher_name': voucher_name}, 'enable_sync')


@frappe.whitelist()
@company_sync_required("LEDGER")
def get_party(company_id = None):

    if company_id == None:
        return Response(json.dumps("Company number not found!", default=str), content_type='application/json')

    all_doc = []

    synced_suppliers = frappe.get_all('Tally Master Sync Log',
        filters={'parenttype': 'Supplier', 'company_number': company_id, 'status': 'SUCCESS'},
        pluck='parent')

    suppliers = frappe.get_all('Supplier', filters = {'name': ['not in', synced_suppliers]}, fields=['*'], limit=get_master_sync_limit()) if is_sync_enabled('Supplier') else []
    for supplier in suppliers:

        if supplier.get('supplier_primary_address'):
            supplier_address = frappe.get_all('Address',
                filters={'name': supplier['supplier_primary_address']},
                fields=['*'])
        else:
            supplier_address = []


        supplier_dict = {
                "Autoid": supplier.name,
                "CompanyNumber": str(company_id),
                "LedgerName": supplier.name,
                "LedgerParent": "Sundry Creditors",
                "LedgerAddress": supplier_address[0]['city'] if supplier_address else '',
                "LedgerState": supplier_address[0]['state'] if supplier_address else '',
                "LedgerCountry": supplier_address[0]['country'] if supplier_address else '',
                "LedgerPincode": supplier_address[0]['pincode'] if supplier_address else '',
                "LedgerMobile": supplier.mobile_no,
                "LedgerGstReg": "Regular" if supplier_address and supplier_address[0]['gst_category'] == 'Registered Regular' else "Unregistered/Consumer",
                "LedgerPan": supplier.pan if supplier_address and supplier_address[0]['gstin'] else '',
                "LedgerGstin": supplier_address[0]['gstin'] if supplier_address and supplier_address[0]['gstin'] else '',
            }

        all_doc.append(supplier_dict)


    sync_master_from = frappe.get_value('TS Tally Company',company_filters(company_id),'sync_master_from')

    start_date = get_datetime(sync_master_from)

    synced_customers = frappe.get_all('Tally Master Sync Log',
        filters={'parenttype': 'Customer', 'company_number': company_id, 'status': 'SUCCESS'},
        pluck='parent')

    customers = frappe.get_all('Customer', filters = {'name': ['not in', synced_customers], 'creation': [">", start_date]}, fields=['*'], limit=get_master_sync_limit()) if is_sync_enabled('Customer') else []

    for customer in customers:
        if customer.get('customer_primary_address'):
            customer_address = frappe.get_all('Address',
                filters={'name': customer['customer_primary_address']},
                fields=['*'])
        else:
            customer_address = []

        customer_dict = {
            "Autoid": customer.name,
            "CompanyNumber": str(company_id),
            "LedgerName": customer.name,
            "LedgerParent": "Sundry Debtors",
            "LedgerAddress": customer_address[0]['city'] if customer_address else '',
            "LedgerState": customer_address[0]['state'] if customer_address else '',
            "LedgerCountry": customer_address[0]['country'] if customer_address else '',
            "LedgerPincode": customer_address[0]['pincode'] if customer_address else '',
            "LedgerMobile": customer.mobile_no,
            "LedgerGstReg": "Regular" if customer_address and customer_address[0]['gst_category'] == 'Registered Regular' else "Unregistered/Consumer",
            "LedgerPan": customer.pan if customer_address and customer_address[0]['gstin'] else '',
            "LedgerGstin": customer_address[0]['gstin'] if customer_address and customer_address[0]['gstin'] else '',
        }

        all_doc.append(customer_dict)


    synced_employees = frappe.get_all('Tally Master Sync Log',
        filters={'parenttype': 'Employee', 'company_number': company_id, 'status': 'SUCCESS'},
        pluck='parent')

    employees = frappe.get_all('Employee', filters = {'name': ['not in', synced_employees]}, fields=['*'], limit=get_master_sync_limit()) if is_sync_enabled('Employee') else []

    for employee in employees:

        employee_dict = {
            "Autoid": employee.name,
            "CompanyNumber": str(company_id),
            "LedgerName": employee.name,
            "LedgerParent": "Sundry Debtors",
            "LedgerAddress": "",
            "LedgerState": '',
            "LedgerCountry": '',
            "LedgerPincode": '',
            "LedgerMobile": employee.cell_number,
            "LedgerGstReg": "",
            "LedgerPan": employee.pan_number if employee.pan_number else '',
            "LedgerGstin": '',
        }

        all_doc.append(employee_dict)


    company_name = frappe.get_value('TS Tally Company', company_filters(company_id), 'company_name')

    synced_accounts = frappe.get_all('Tally Master Sync Log',
        filters={'parenttype': 'Account', 'company_number': company_id, 'status': 'SUCCESS'},
        pluck='parent')

    # Debtors / Creditors accounts are sent as the party ledger in vouchers, so skip them.
    accounts = frappe.get_all('Account', filters = {
        'name': ['not in', synced_accounts],
        'company': company_name,
        'is_group': 0,
        'disabled': 0,
        'account_type': ['not in', ['Receivable', 'Payable']],
        'custom_tally_parent_account': ['is', 'set'],
    }, fields=['name', 'account_name', 'custom_tally_parent_account'], limit=get_master_sync_limit()) if is_sync_enabled('Account') else []

    for account in accounts:

        account_dict = {
            "Autoid": account.account_name,
            "CompanyNumber": str(company_id),
            "LedgerName": account.account_name,
            "LedgerParent": account.custom_tally_parent_account,
            "LedgerAddress": "",
            "LedgerState": '',
            "LedgerCountry": '',
            "LedgerPincode": '',
            "LedgerMobile": '',
            "LedgerGstReg": "",
            "LedgerPan": '',
            "LedgerGstin": '',
        }

        all_doc.append(account_dict)


    final_voucher = ({
        "status": True,
        "VOUCHERDETAILS": {
            "LEDGER": all_doc
        }
    })

    final_voucher = Response(json.dumps(final_voucher, default=str), content_type='application/json')
    final_voucher.status_code = 200

    return final_voucher





def _update_sync_log(doctype, docname, parentfield, company_id, company_name, party_name, status, sync_time):
    existing = frappe.db.get_value('Tally Master Sync Log', {
        'parent': docname,
        'parenttype': doctype,
        'company_number': company_id
    }, 'name')

    if existing:
        frappe.db.set_value('Tally Master Sync Log', existing, {
            'tally_auto_id': party_name,
            'status': status,
            'sync_time': sync_time
        })
    else:
        max_idx = frappe.db.count('Tally Master Sync Log', {
            'parent': docname,
            'parenttype': doctype
        })

        sync_log = frappe.new_doc('Tally Master Sync Log')
        sync_log.parent = docname
        sync_log.parenttype = doctype
        sync_log.parentfield = parentfield
        sync_log.idx = max_idx + 1
        sync_log.company_number = company_id
        sync_log.company_name = company_name
        sync_log.tally_auto_id = party_name
        sync_log.status = status
        sync_log.sync_time = sync_time
        sync_log.db_insert()

    frappe.db.set_value(doctype, docname, 'modified', frappe.utils.now())




@frappe.whitelist()
def fetch_response(response, company_id=None):
    frappe.log_error(title="Tally fetch_response - Raw Input", message=f"company_id: {company_id}\nresponse type: {type(response).__name__}\nresponse: {response[:5000] if isinstance(response, str) else json.dumps(response, default=str)[:5000]}")
    data = json.loads(response) if isinstance(response, str) else response
    parties = data.get("LEDGER RESPONSE", [])
    frappe.log_error(title="Tally fetch_response - Parsed Data", message=f"Number of parties: {len(parties)}\nParties: {json.dumps(parties, default=str)[:5000]}")

    company_name = frappe.get_value('TS Tally Company', {'company_number': company_id}, 'company_name') if company_id else None

    for party in parties:
        party_name = party.get("AUTOID")
        status = party.get("STATUS")
        import_date = party.get("IMPORTDATE")
        import_time = party.get("IMPORTTIME")

        if not party_name:
            continue

        import_date_obj = datetime.strptime(import_date, "%Y%m%d").date()
        import_time_obj = datetime.strptime(import_time, "%H:%M:%S").time()
        sync_time = datetime.combine(import_date_obj, import_time_obj)

        updated = False

        # Parties are sent to Tally by their ID.
        for doctype in ('Customer', 'Supplier', 'Employee'):
            if frappe.db.exists(doctype, party_name):
                _update_sync_log(doctype, party_name, 'custom_tally_sync_log', company_id, company_name, party_name, status, sync_time)
                updated = True

        account_filters = {"account_name": party_name}
        if company_name:
            account_filters["company"] = company_name
        acc_doc = frappe.db.exists("Account", account_filters)
        if acc_doc:
            _update_sync_log('Account', acc_doc, 'custom_tally_sync_log', company_id, company_name, party_name, status, sync_time)
            updated = True

        if not updated:
            frappe.log_error(f"Neither Customer nor Supplier found for Tally AUTOID: {party_name}", "Tally Sync Error")

    frappe.db.commit()

    return Response(json.dumps({
        "status":True,
        "message":"Updated successfully"
    }, default=str), content_type='application/json')
