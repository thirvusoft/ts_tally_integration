import frappe, importlib
from ts_tally_integration.tally_integration.utils.api.ledger_items import fill_gst_rates, group_item_taxes, gst_ledger_suffix
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_voucher_sync_limit
from datetime import datetime
import json
from werkzeug.wrappers import Response
from frappe.utils import getdate, today


DEFAULT_COST_CATEGORY = "Primary Cost Category"


def get_tally_cost_center(doc):
    return doc.cost_center.split("-", 1)[0].strip() if doc.cost_center else ""

def get_tally_cost_category(doc):
    return DEFAULT_COST_CATEGORY if get_tally_cost_center(doc) else ""


NON_EXPENSE_LEDGER_TYPES = (
    "Tax", "Bank", "Payable", "Chargeable", "Round Off",
    "Stock In Hand", "Cost of Goods Sold",
    "Stock Received But Not Billed", "Stock Adjustment",
    "Expenses Included In Valuation",
    "Expenses Included In Asset Valuation",
)


def posted_as_own_ledger(account):
    """True when the GL row for `account` is sent by the Fixed Asset,
    Chargeable or expense branch of get_purchase_invoice rather than as a stock
    item line."""
    if not account:
        return False
    info = frappe.db.get_value('Account', account, ['account_type', 'root_type'], as_dict=True) or {}
    account_type = info.get('account_type')
    if account_type in ('Bank', 'Fixed Asset', 'Chargeable'):
        return True
    return (
        account_type in ("Expense Account", "Indirect Expense", "Direct Expense", "Depreciation")
        or (info.get('root_type') == "Expense" and account_type not in NON_EXPENSE_LEDGER_TYPES)
    )


@frappe.whitelist()
@company_sync_required("VOUCHER")
def get_purchase_invoice(company_id=None):

    if company_id == None:
        return Response(json.dumps('Company Number not found!', default=str), content_type='application/json')

    stock = frappe.get_value('TS Tally Company', company_filters(company_id), ['stock'])
    if stock == 'Non-Inventory':
        empty = ({
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": []
                }
            })
        return Response(json.dumps(empty, default=str), content_type='application/json')

    enable_sync = frappe.get_value('Voucher Sync Control', {'voucher_name': 'Purchase Invoice (Inventory)'}, ['enable_sync'])
    if not enable_sync:
        final_voucher = {
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": []
                }
            }
        return Response(json.dumps(final_voucher, default=str), content_type='application/json')

    company_name = frappe.get_value('TS Tally Company', company_filters(company_id), ['company_name'])
    cost_center = frappe.get_value('TS Tally Company', company_filters(company_id), ['cost_center'])
    default_warehouse = frappe.db.get_single_value('TS Tally Settings', 'default_warehouse')

    company_address_link = frappe.get_all('Dynamic Link', filters={'link_doctype': 'Company', 'link_name': company_name}, fields=['parent'])
    company_address = frappe.get_all('Address', filters={'name': company_address_link[0]['parent']} if company_address_link else {}, fields=['*'])
    company_gst = frappe.get_value('Company', {'name': company_name}, ['gstin'])

    all_vouchers = []

    sync_from = frappe.get_value('TS Tally Company', company_filters(company_id), 'sync_from')

    start_date = getdate(sync_from)
    sync_to = frappe.get_value('TS Tally Company', company_filters(company_id), 'sync_to')
    end_date = getdate(sync_to) if sync_to else getdate(today())

    purchase_filters = {'company':company_name, 'is_return':0, 'docstatus':1, 'is_opening': 'No',
                                         'custom_tally_guid': ['is', 'not set'], 'posting_date': ['between', [start_date, end_date]]}
    if cost_center:
        purchase_filters['cost_center'] = cost_center
    purchase_list = frappe.get_all('Purchase Invoice',
                                filters=purchase_filters,
                                fields=['*'], order_by='posting_date asc', limit=get_voucher_sync_limit()
                                )

    for doc in purchase_list:
        tax_processed = False
        items_processed = False
        if doc.supplier_address:
            cus_address = frappe.get_all('Address', filters={'name': doc.supplier_address}, fields=['*'])
        else:
            cus_address = []
        supplier_pan = frappe.get_value('Supplier', {'name': doc.supplier}, ['pan'])

        cus_ship_link = frappe.get_all('Dynamic Link', filters={'link_doctype': 'supplier', 'link_name': doc['supplier']}, fields=['parent'])
        cus_ship_address = frappe.get_all('Address', filters={'name': cus_ship_link[0]['parent']} if cus_ship_link else {}, fields=['*'])

        cust_gstin = frappe.get_doc('Supplier', doc.supplier)

        gst_category = {
            "Unregistered": "Unregistered/Consumer",
            "Registered Regular": "Regular",
            "Registered Composition": "Composition",
            "SEZ": "Regular - SEZ"
        }.get(cust_gstin.gst_category, cust_gstin.gst_category)

        gl_entry = frappe.get_all('GL Entry', filters = {'voucher_no':doc.name}, fields = ['*'])
        gl_entry = gl_entry[::-1]

        # GST accounts are sent as "Input Tax ... @ rate" lines built from the
        # items. Other tax accounts (TCS, TDS) are not, so they go out as their
        # own ledger line; skipping them left Cr above Dr.
        gst_accounts = set(frappe.get_all(
            'Purchase Taxes and Charges',
            filters={'parent': doc.name, 'gst_tax_type': ['is', 'set']},
            pluck='account_head'
        ))


        for invoice in gl_entry:
            amount = invoice['credit'] if 'credit' in invoice and invoice['credit'] else invoice['debit']
            cr_dr = "Cr" if 'credit' in invoice and invoice['credit'] else "Dr"

            account_info = frappe.get_value('Account', invoice['account'], ['account_type', 'root_type'], as_dict=True) or {}
            account_type = account_info.get('account_type')
            root_type = account_info.get('root_type')
            account = (invoice.get("account") or "").split(" - ")[0]
            if account in ["Stock In Hand", "Cost of Goods Sold", "Stock Received But Not Billed",]:
                # The stock lines cover every stock item at once, so a second
                # stock GL row must not repeat them.
                if items_processed:
                    continue
                items_processed = True

                ledgername = invoice['account']
                parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                ledger_suffix = None

                purchase_item = frappe.get_all('Purchase Invoice Item', filters={'parent':doc.name}, fields=['*'])

                fill_gst_rates(purchase_item, 'Purchase Taxes and Charges', doc.name)
                # Items booked to an expense or fixed asset account already go out
                # as that account's own ledger line below; listing them here as
                # well counted them twice and left Dr above Cr.
                purchase_item = [
                    item for item in purchase_item
                    if not posted_as_own_ledger(item.get('expense_account'))
                ]

                taxes = frappe.db.get_all(
                    "Purchase Taxes and Charges",
                    filters={"parent": doc.name},
                    fields=["category"]
                )

                use_valuation = any(t.category == "Valuation and Total" or "Valuation" for t in taxes)


                for item in purchase_item:

                    item_rate = 0
                    item_amount = 0

                    if use_valuation:
                        if doc.update_stock:
                            item_rate = round(item['valuation_rate'], 2)
                            item_amount = round(item['qty'] * item['valuation_rate'], 2)
                        else:
                            # fallback when valuation exists but stock is not updated
                            item_rate = round(item['net_rate'], 2)
                            item_amount = round(item['taxable_value'], 2)
                    else:
                        item_rate = round(item['net_rate'], 2)
                        item_amount = round(item['taxable_value'], 2)


                    hsn_desc = frappe.get_value('GST HSN Code', {'name': item.get('gst_hsn_code')}, 'description')
                    gst_hsn_description = hsn_desc.replace('\n', ' ') if hsn_desc else ""

                    ledger_suffix = gst_ledger_suffix(item, doc.name)


                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": 'ERP Purchase',
                        "VoucherTypeParent": 'Purchase',
                        "LedgerName": f"Purchase @ {(ledger_suffix)}",
                        "LedgerParent": 'Purchase Accounts',

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                        "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": get_tally_cost_category(doc),
                        "CostCentre": get_tally_cost_center(doc),
                        "Stockitem": item['item_code'],
                        "Godown": (item.get('warehouse') or default_warehouse).split('-')[0].strip() if (item.get('warehouse') or default_warehouse) else None,
                        "BatchNo": "",
                        "Quantity": item['qty'],
                        "Rate": item_rate,
                        "Discount": "",
                        "Amount": item_amount,
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability": "Taxable" if item.get('cgst_rate') else "Exempt",
                        "GstOvrdnTypeofsupply":"Goods",
                        "GstHsnName":item['gst_hsn_code'] if item['gst_hsn_code'] else "",
                        "GstHsnDescription": gst_hsn_description,
                        "CgstGstRateDutyhead":"CGST",
                        "CgstGstRateValuationtype":"Based on Value",
                        "CgstGstRate":item['cgst_rate'] if item['cgst_rate'] else "",
                        "SgstGstRateDutyhead":"SGST/UTGST",
                        "SgstGstRateValuationtype":"Based on Value",
                        "SgstGstRate":item['sgst_rate'] if item['sgst_rate'] else "",
                        "IgstGstRateDutyhead":"IGST",
                        "IgstGstRateValuationtype":"Based on Value",
                        "IgstGstRate": item['sgst_rate'] + item['cgst_rate'] if item['sgst_rate'] and item['cgst_rate'] else "",
                        "Reference": doc.bill_no if doc.get('bill_no') else "",
                        "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)
                purchase_item_processed = True  

                if purchase_item_processed:
                    continue

            # --------------------------------- The BELOW block of code is only for TAX ---------------------------------------------


            elif account_type == 'Tax' and invoice['account'] in gst_accounts:
                if not tax_processed:
                    ledgername = invoice['account']
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')
                    tax_processed = True

                    items_tax = group_item_taxes(fill_gst_rates(
                            frappe.get_all('Purchase Invoice Item', filters={'parent': doc.name}, fields=['*']),
                            'Purchase Taxes and Charges', doc.name))

                    for item in items_tax:
                        if not item['gst_treatment'] == 'Exempted':
                            if item['cgst_rate']:
                                ledger_dict = {
                                    "Autoid": doc.name,
                                    "CompanyNumber": str(company_id),
                                    "TallyMasterid": 1,
                                    "Voucherid": doc.name,
                                    "VoucherNumber": doc.name,
                                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "VoucherType": 'ERP Purchase',
                                    "VoucherTypeParent": 'Purchase',
                                    "LedgerName": f"Input Tax CGST @ {item['cgst_rate']}",
                                    "LedgerParent": parent_account,

                                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                                    "BillName": doc.name,
                                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "CrDr": cr_dr,
                                    "CostCategory": "",
                                    "CostCentre": "",
                                    "Stockitem": "",
                                    "Godown": "",
                                    "BatchNo": "",
                                    "Quantity": "",
                                    "Rate": "",
                                    "Discount": "",
                                    "Amount": round(item['cgst_amount'], 2),
                                    "OrderNo": "",
                                    "OrderDate": "",
                                    "TrackingNo": "",
                                    "TrackingDate": "",
                                    "TermsOfPayment": "",
                                    "OtherRef": "",
                                    "TermsOfDelivery1": "",
                                    "TermsOfDelivery2": "",
                                    "DispatchDocNo": "",
                                    "ReceiptDocNo": "",
                                    "DispatchedThrough": "",
                                    "Destination": "",
                                    "CarrierName": "",
                                    "BillOfLanding": "",
                                    "BillOfLandingDate": "",
                                    "VehicleNo": "",

                                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                    "CmpGstRegistrationType":gst_category,
                                    "CmpGstin":company_gst,
                                    "CmpGstState":company_address[0]['state'] if company_address else "",
                                    "GstOvrdnTaxability":"",
                                    "GstOvrdnTypeofsupply":"",
                                    "GstHsnName":"",
                                    "GstHsnDescription":"",
                                    "CgstGstRateDutyhead":"",
                                    "CgstGstRateValuationtype":"",
                                    "CgstGstRate":"",
                                    "SgstGstRateDutyhead":"",
                                    "SgstGstRateValuationtype":"",
                                    "SgstGstRate":"",
                                    "IgstGstRateDutyhead":"",
                                    "IgstGstRateValuationtype":"",
                                    "IgstGstRate":"",
                                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                                    "Narration": ""
                                    }

                                all_vouchers.append(ledger_dict)


                            if item['sgst_rate']:
                                ledger_dict = {
                                    "Autoid": doc.name,
                                    "CompanyNumber": str(company_id),
                                    "TallyMasterid": 1,
                                    "Voucherid": doc.name,
                                    "VoucherNumber": doc.name,
                                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "VoucherType": 'ERP Purchase',
                                    "VoucherTypeParent": 'Purchase',
                                    "LedgerName": f"Input Tax SGST @ {item['cgst_rate']}",
                                    "LedgerParent": parent_account,

                                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                                    "BillName": doc.name,
                                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "CrDr": cr_dr,
                                    "CostCategory": "",
                                    "CostCentre": "",
                                    "Stockitem": "",
                                    "Godown": "",
                                    "BatchNo": "",
                                    "Quantity": "",
                                    "Rate": "",
                                    "Discount": "",
                                    "Amount": round(item['cgst_amount'], 2),
                                    "OrderNo": "",
                                    "OrderDate": "",
                                    "TrackingNo": "",
                                    "TrackingDate": "",
                                    "TermsOfPayment": "",
                                    "OtherRef": "",
                                    "TermsOfDelivery1": "",
                                    "TermsOfDelivery2": "",
                                    "DispatchDocNo": "",
                                    "ReceiptDocNo": "",
                                    "DispatchedThrough": "",
                                    "Destination": "",
                                    "CarrierName": "",
                                    "BillOfLanding": "",
                                    "BillOfLandingDate": "",
                                    "VehicleNo": "",

                                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                    "CmpGstRegistrationType":gst_category,
                                    "CmpGstin":company_gst,
                                    "CmpGstState":company_address[0]['state'] if company_address else "",
                                    "GstOvrdnTaxability":"",
                                    "GstOvrdnTypeofsupply":"",
                                    "GstHsnName":"",
                                    "GstHsnDescription":"",
                                    "CgstGstRateDutyhead":"",
                                    "CgstGstRateValuationtype":"",
                                    "CgstGstRate":"",
                                    "SgstGstRateDutyhead":"",
                                    "SgstGstRateValuationtype":"",
                                    "SgstGstRate":"",
                                    "IgstGstRateDutyhead":"",
                                    "IgstGstRateValuationtype":"",
                                    "IgstGstRate":"",
                                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                                    "Narration": ""
                                    }

                                all_vouchers.append(ledger_dict)

                            if item['igst_rate']:
                                ledger_dict = {
                                    "Autoid": doc.name,
                                    "CompanyNumber": str(company_id),
                                    "TallyMasterid": 1,
                                    "Voucherid": doc.name,
                                    "VoucherNumber": doc.name,
                                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "VoucherType": 'ERP Purchase',
                                    "VoucherTypeParent": 'Purchase',
                                    "LedgerName": f"{ledgername.split(' - ')[0]} @ {item['igst_rate']}",
                                    "LedgerParent": parent_account,

                                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                                    "BillName": doc.name,
                                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                    "CrDr": cr_dr,
                                    "CostCategory": "",
                                    "CostCentre": "",
                                    "Stockitem": "",
                                    "Godown": "",
                                    "BatchNo": "",
                                    "Quantity": "",
                                    "Rate": "",
                                    "Discount": "",
                                    "Amount": round(item['igst_amount'], 2),
                                    "OrderNo": "",
                                    "OrderDate": "",
                                    "TrackingNo": "",
                                    "TrackingDate": "",
                                    "TermsOfPayment": "",
                                    "OtherRef": "",
                                    "TermsOfDelivery1": "",
                                    "TermsOfDelivery2": "",
                                    "DispatchDocNo": "",
                                    "ReceiptDocNo": "",
                                    "DispatchedThrough": "",
                                    "Destination": "",
                                    "CarrierName": "",
                                    "BillOfLanding": "",
                                    "BillOfLandingDate": "",
                                    "VehicleNo": "",

                                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                    "CmpGstRegistrationType":gst_category,
                                    "CmpGstin":company_gst,
                                    "CmpGstState":company_address[0]['state'] if company_address else "",
                                    "GstOvrdnTaxability":"",
                                    "GstOvrdnTypeofsupply":"",
                                    "GstHsnName":"",
                                    "GstHsnDescription":"",
                                    "CgstGstRateDutyhead":"",
                                    "CgstGstRateValuationtype":"",
                                    "CgstGstRate":"",
                                    "SgstGstRateDutyhead":"",
                                    "SgstGstRateValuationtype":"",
                                    "SgstGstRate":"",
                                    "IgstGstRateDutyhead":"",
                                    "IgstGstRateValuationtype":"",
                                    "IgstGstRate":"",
                                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                                    "Narration": ""
                                    }

                                all_vouchers.append(ledger_dict)
                # --------------------------------- The ABOVE block of code is only for TAX ---------------------------------------------


            elif account_type in ('Bank', 'Fixed Asset', 'Tax'):
                ledgername = invoice['account']
                parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                ledger_dict = {
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": 'ERP Purchase',
                    "VoucherTypeParent": 'Purchase',
                    "LedgerName": ledgername.split(" - ")[0],
                    "LedgerParent": parent_account,

                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                    "BillName": doc.name,
                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": cr_dr,
                    "CostCategory": "",
                    "CostCentre": "",
                    "Stockitem": "",
                    "Godown": "",
                    "BatchNo": "",
                    "Quantity": "",
                    "Rate": "",
                    "Discount": "",
                    "Amount": round(amount, 2),
                    "OrderNo": "",
                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "",
                    "TermsOfDelivery2": "",
                    "DispatchDocNo": "",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "",
                    "Destination": "",
                    "CarrierName": "",
                    "BillOfLanding": "",
                    "BillOfLandingDate": "",
                    "VehicleNo": "",

                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                    "CmpGstRegistrationType":gst_category,
                    "CmpGstin":company_gst,
                    "CmpGstState":company_address[0]['state'] if company_address else "",
                    "GstOvrdnTaxability":"",
                    "GstOvrdnTypeofsupply":"",
                    "GstHsnName":"",
                    "GstHsnDescription":"",
                    "CgstGstRateDutyhead":"",
                    "CgstGstRateValuationtype":"",
                    "CgstGstRate":"",
                    "SgstGstRateDutyhead":"",
                    "SgstGstRateValuationtype":"",
                    "SgstGstRate":"",
                    "IgstGstRateDutyhead":"",
                    "IgstGstRateValuationtype":"",
                    "IgstGstRate":"",
                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                    "Narration": ""
                }

                all_vouchers.append(ledger_dict)


            elif account_type == 'Payable':
                ledgername = doc.supplier
                parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                ledger_dict = {
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": 'ERP Purchase',
                    "VoucherTypeParent": 'Purchase',
                    "LedgerName": ledgername,
                    "LedgerParent": parent_account,

                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                    "BillName": doc.name,
                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": cr_dr,
                    "CostCategory": get_tally_cost_category(doc),
                    "CostCentre": get_tally_cost_center(doc),
                    "Stockitem": "",
                    "Godown": "",
                    "BatchNo": "",
                    "Quantity": "",
                    "Rate": "",
                    "Discount": "",
                    "Amount": round(amount, 2),
                    "OrderNo": "",
                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "",
                    "TermsOfDelivery2": "",
                    "DispatchDocNo": "",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "",
                    "Destination": "",
                    "CarrierName": "",
                    "BillOfLanding": "",
                    "BillOfLandingDate": "",
                    "VehicleNo": "",

                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                    "CmpGstRegistrationType":gst_category,
                    "CmpGstin":company_gst,
                    "CmpGstState":company_address[0]['state'] if company_address else "",
                    "GstOvrdnTaxability":"",
                    "GstOvrdnTypeofsupply":"",
                    "GstHsnName":"",
                    "GstHsnDescription":"",
                    "CgstGstRateDutyhead":"",
                    "CgstGstRateValuationtype":"",
                    "CgstGstRate":"",
                    "SgstGstRateDutyhead":"",
                    "SgstGstRateValuationtype":"",
                    "SgstGstRate":"",
                    "IgstGstRateDutyhead":"",
                    "IgstGstRateValuationtype":"",
                    "IgstGstRate":"",
                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                    "Narration": ""
                }

                all_vouchers.append(ledger_dict)


            elif (
                account_type in ("Expense Account", "Indirect Expense", "Direct Expense", "Depreciation")
                or (
                    root_type == "Expense"
                    and account_type not in (
                        "Tax", "Bank", "Payable", "Chargeable", "Round Off",
                        "Stock In Hand", "Cost of Goods Sold",
                        "Stock Received But Not Billed", "Stock Adjustment",
                        "Expenses Included In Valuation",
                        "Expenses Included In Asset Valuation",
                    )
                )
            ):

                if invoice.get("debit"):
                    ledgername = invoice['account']
                    amount = invoice['debit']
                    cr_dr = "Dr"

                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": 'ERP Purchase',
                        "VoucherTypeParent": 'Purchase',
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                        "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Reference": doc.bill_no if doc.get('bill_no') else "",
                        "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)



                # Credit entry
                if invoice.get("credit"):
                    ledgername = invoice['account']
                    amount = invoice['credit']
                    cr_dr = "Cr"

                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": 'ERP Purchase',
                        "VoucherTypeParent": 'Purchase',
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                        "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Reference": doc.bill_no if doc.get('bill_no') else "",
                        "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)



            elif account_type == "Chargeable":
                if invoice.get("debit"):
                    ledgername = invoice['account']
                    amount = invoice['debit']
                    cr_dr = "Dr"

                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": 'ERP Purchase',
                        "VoucherTypeParent": 'Purchase',
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                        "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Reference": doc.bill_no if doc.get('bill_no') else "",
                        "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)



                # Credit entry
                if invoice.get("credit"):
                    ledgername = invoice['account']
                    amount = invoice['credit']
                    cr_dr = "Cr"

                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": 'ERP Purchase',
                        "VoucherTypeParent": 'Purchase',
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                        "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Reference": doc.bill_no if doc.get('bill_no') else "",
                        "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)





            elif account_type == 'Round Off':
                ledgername = 'Roundoff'
                parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                ledger_dict = {
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": 'ERP Purchase',
                    "VoucherTypeParent": 'Purchase',
                    "LedgerName": ledgername.split(" - ")[0],
                    "LedgerParent": parent_account,

                    "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Creditors" else "", 
                    "LedgerGstReg": gst_category if parent_account== "Sundry Creditors" else "", 
                    "LedgerPan": supplier_pan if parent_account== "Sundry Creditors" else "", 
                    "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",

                    "BillName": doc.name,
                    "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": cr_dr,
                    "CostCategory": "",
                    "CostCentre": "",
                    "Stockitem": "",
                    "Godown": "",
                    "BatchNo": "",
                    "Quantity": "",
                    "Rate": "",
                    "Discount": "",
                    "Amount": round(amount, 2),
                    "OrderNo": "",
                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "",
                    "TermsOfDelivery2": "",
                    "DispatchDocNo": "",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "",
                    "Destination": "",
                    "CarrierName": "",
                    "BillOfLanding": "",
                    "BillOfLandingDate": "",
                    "VehicleNo": "",

                    "BuyerName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerMailingName": doc.supplier if parent_account== "Sundry Creditors" else "",
                    "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Creditors" and cus_address else "",
                    "BuyerGstReg": gst_category if parent_account== "Sundry Creditors" else "",
                    "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_address else "",

                    "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Creditors" else "",
                    "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Creditors" and cus_ship_address else "",
                    "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Creditors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                    "CmpGstRegistrationType":gst_category,
                    "CmpGstin":company_gst,
                    "CmpGstState":company_address[0]['state'] if company_address else "",
                    "GstOvrdnTaxability":"",
                    "GstOvrdnTypeofsupply":"",
                    "GstHsnName":"",
                    "GstHsnDescription":"",
                    "CgstGstRateDutyhead":"",
                    "CgstGstRateValuationtype":"",
                    "CgstGstRate":"",
                    "SgstGstRateDutyhead":"",
                    "SgstGstRateValuationtype":"",
                    "SgstGstRate":"",
                    "IgstGstRateDutyhead":"",
                    "IgstGstRateValuationtype":"",
                    "IgstGstRate":"",
                    "Reference": doc.bill_no if doc.get('bill_no') else "",
                    "ReferenceDate": datetime.strptime(str(doc.bill_date), '%Y-%m-%d').strftime('%d-%m-%Y') if doc.get('bill_date') else "",
                    "Narration": ""
                }

                all_vouchers.append(ledger_dict)

    final_voucher = ({
        "status": True,
        "VOUCHERDETAILS": {
            "VOUCHER": all_vouchers
        }
    })

    final_voucher = final_voucher
    final_voucher = Response(json.dumps(final_voucher, default=str), content_type='application/json')
    final_voucher.status_code = 200

    return final_voucher





@frappe.whitelist()
def fetch_response(response):
    data = json.loads(response) if isinstance(response, str) else response

    purchase_response = data.get("PURCHASE RESPONSE", [])

    for item in purchase_response:
        purchase_entry = item.get("AUTOID")
        guid = item.get("GUID")
        ref_no = item.get("REFNO")
        import_date = item.get("IMPORTDATE")
        import_time = item.get("IMPORTTIME")

        if not purchase_entry:
            frappe.log_error(f"No AUTOID found in response: {item}", "Tally Purchase Sync Warning")
            continue

        existing_purchase = frappe.db.get_value("Purchase Invoice", {"name": purchase_entry}, "name")
        if existing_purchase:
            import_date_obj = datetime.strptime(import_date, "%Y%m%d").date()
            import_time_obj = datetime.strptime(import_time, "%H:%M:%S").time()
            sync_datetime = datetime.combine(import_date_obj, import_time_obj)

            frappe.db.set_value("Purchase Invoice", existing_purchase, {
                "custom_tally_auto_id": purchase_entry,
                "custom_tally_guid": guid,
                "custom_tally_refno": ref_no,
                "custom_sync_time": sync_datetime
            })

    frappe.db.commit()

    return Response(json.dumps({
        "status":True,
        "message":"Updated successfully"
    }), content_type='application/json')
