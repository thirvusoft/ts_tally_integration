import frappe, importlib
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_voucher_sync_limit
import json
from datetime import datetime
from werkzeug.wrappers import Response
from frappe.utils import getdate, today


@frappe.whitelist()
@company_sync_required("VOUCHER")
def get_payment_entry_pay(company_id=None):
    redirect_record = frappe.get_value(
        'Tally Different Function',
        {
            'parent': 'TS Tally Settings',
            'tally_function_name': 'get_payment_entry_pay'
        },
        'redirection_path'
    )

    if redirect_record:

        module_path, func_name = redirect_record.rsplit('.', 1)

        custom_module = importlib.import_module(module_path)

        func = getattr(custom_module, func_name)

        return func(company_id)

    else:
        if not company_id:
            return Response(json.dumps("Company ID is not found!", default=str),content_type='application/json', status=404)

        enable_sync = frappe.get_value('Voucher Sync Control', {'voucher_name': 'Payment Entry Pay'}, ['enable_sync'])
        if not enable_sync:
            final_voucher = {
                "status": True,
                "VOUCHERDETAILS": {
                    "VOUCHER": []
                    }
                }

            return Response(json.dumps(final_voucher, default=str), content_type='application/json')

        company_name = frappe.get_value("TS Tally Company",company_filters(company_id),fieldname="company_name")
        cost_center = frappe.get_value('TS Tally Company', company_filters(company_id), 'cost_center')

        sync_from = frappe.get_value('TS Tally Company',company_filters(company_id),'sync_from')

        start_date = getdate(sync_from)
        sync_to = frappe.get_value('TS Tally Company', company_filters(company_id), 'sync_to')
        end_date = getdate(sync_to) if sync_to else getdate(today())

        if not company_name:
            return Response(json.dumps("Company is not found!", default=str),content_type='application/json', status=404)

        # Fetch all Payment Entries which are not yet synced with Tally
        pe_filters = {"docstatus": 1, "company": company_name, "payment_type": "Pay",
                    "custom_tally_guid": ["is", "not set"], 'posting_date': ['between', [start_date, end_date]]}
        if cost_center:
            pe_filters["cost_center"] = cost_center
        doc_list = frappe.get_all("Payment Entry",
            filters=pe_filters,
            fields=["*"], order_by='posting_date asc', limit=get_voucher_sync_limit())

        list_of_entries = []

        for doc in doc_list:

            posting_date = datetime.strptime(
                str(doc.posting_date), '%Y-%m-%d'
            ).strftime('%Y%m%d')

            # Get GL Entries of this Payment Entry
            gl_entries = frappe.get_all("GL Entry",
                filters={"voucher_no": doc.name},
                fields=["party_type", "party", "debit", "credit", "account"])

            # Loop through each GL Entry and generate Tally lines
            for gl in gl_entries:

                party_type = gl.party_type
                party_name = gl.party

                # ---------------- PARTY DETAILS ----------------
                gst_category = ""
                gstin = ""
                pan = None
                ledger_name = ""
                ledger_parent = ""

                if party_type == "Customer":
                    cust = frappe.get_doc("Customer", party_name)
                    gst_category = {
                        "Unregistered": "Unregistered/Consumer",
                        "Registered Regular": "Regular",
                        "Registered Composition": "Composition",
                        "SEZ": "Regular - SEZ"
                    }.get(cust.gst_category, cust.gst_category)

                    ledger_name = party_name
                    gstin = cust.gstin or ""
                    pan = cust.pan or None

                elif party_type == "Supplier":
                    supp = frappe.get_doc("Supplier", party_name)
                    gst_category = supp.supplier_type or ""
                    gstin = supp.gstin or ""
                    pan = supp.pan or None
                    ledger_name = party_name

                elif party_type == "Employee":
                    emp = frappe.get_doc("Employee", party_name)
                    ledger_name = party_name
                    pan = emp.pan_number or None
                    gst_category = "Employee"

                else:
                    # no party (Bank / Cash / Charges)
                    acc = frappe.get_doc("Account", gl.account)
                    ledger_name = acc.custom_tally_account_name if acc.custom_tally_account_name else acc.account_name
                    gst_category = ""
                    gstin = ""
                    pan = ""

                # Always get parent account
                ledger_parent = frappe.db.get_value("Account", gl.account, "custom_tally_parent_account") or ""

                # --------------- DR/CR LOGIC ----------------
                if gl.debit > 0:
                    crdr = "Dr"
                    amount = float(gl.debit)
                else:
                    crdr = "Cr"
                    amount = float(gl.credit)

                # ---------------- BUILD ENTRY ----------------
                entry = {
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": "",
                    "VoucherNumber": doc.name,
                    "VoucherDate": posting_date,
                    "VoucherType": "ERP Payment",
                    "VoucherTypeParent": "Payment",
                    "LedgerName": ledger_name,
                    "LedgerParent": ledger_parent,
                    "LedgerAddress": "",
                    "LedgerState": "",
                    "LedgerCountry": "",
                    "LedgerPincode": "",
                    "LedgerMobile": "",
                    "LedgerGstReg": gst_category,
                    "LedgerGstin": gstin,
                    "LedgerPan": pan,
                    "BillName": "",
                    "BillDate": "",
                    "PlaceOfSupply": "",
                    "TransactionDate": posting_date,
                    "CrDr": crdr,
                    "Amount": amount,
                    "CostCategory1": "",
                    "CostCentre1": doc.cost_center.split(" - ")[0] if doc.cost_center else "",
                    "CostCategory2": "",
                    "CostCentre2": "",
                    "CostCategory3": "",
                    "CostCentre3": "",
                    "CostCategory4": "",
                    "CostCentre4": "",
                    "CostCategory5": "",
                    "CostCentre5": "",
                    "BranchCode": "",
                    "Location": "",
                    "State": "",
                    "Narration": doc.remarks.replace("\n", ". ") if doc.remarks else None
                }

                list_of_entries.append(entry)

        # FINAL JSON RESPONSE
        response_payment = {
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": list_of_entries
                }
            }

        return Response(
            json.dumps(response_payment, default=str),
            content_type='application/json',
            status=200
        )









@frappe.whitelist()
def fetch_response(response):

    data = json.loads(response) if isinstance(response, str) else response

    payment_response = data.get("PAYMENT RESPONSE", [])

    for response in payment_response:
        payment_entry = response.get("AUTOID")
        guid = response.get("GUID")
        ref_no = response.get("REFNO")
        import_date = response.get("IMPORTDATE")
        import_time = response.get("IMPORTTIME")

        if not payment_entry:
            continue

        existing_payment = frappe.db.get_value("Payment Entry", {"name": payment_entry}, "name")
        if existing_payment:

            import_date = datetime.strptime(import_date, "%Y%m%d").date()
            import_time = datetime.strptime(import_time, "%H:%M:%S").time()

            frappe.db.set_value("Payment Entry", existing_payment, {
                "custom_tally_auto_id": payment_entry,
                "custom_tally_guid": guid,
                "custom_tally_refno": ref_no,
                "custom_sync_time": datetime.combine(import_date, import_time)
            })

    frappe.db.commit()

    return Response(json.dumps({
        "status":True,
        "message":"Updated successfully"
    }), content_type='application/json')


