import frappe
from frappe.utils import flt


def items_without_own_ledger(items, account_field, own_ledger_types):
    """Drop the items whose account the voucher already sends as its own ledger
    line (an account type in `own_ledger_types`), so the item lines built from
    the stock / income GL row do not count them a second time."""
    account_types = {}
    out = []
    for item in items:
        account = item.get(account_field)
        if account and account not in account_types:
            account_types[account] = frappe.db.get_value('Account', account, 'account_type')
        if account and account_types[account] in own_ledger_types:
            continue
        out.append(item)
    return out


GST_TYPES = ('cgst', 'sgst', 'igst')

# Sales / Purchase ledger suffix for items that carry no GST rate.
NON_TAXABLE_SUFFIX = {
    'Exempted': 'Exempt',
    'Nil-Rated': 'Nil Rated',
    'Non-GST': 'Non GST',
    'Zero-Rated': 'Zero Rated',
}


def fill_gst_rates(items, taxes_doctype, parent):
    """Items with no GST rate of their own take CGST / SGST / IGST from the
    taxes table. Only rows with a GST Tax Type count, since the Rate field is
    also used for other charges. Each item's GST amount is worked out from its
    taxable value, and any paisa left over goes on the last item so the tax
    lines still add up to the taxes table."""
    taxable = [
        item for item in items
        if item.get('gst_treatment') not in NON_TAXABLE_SUFFIX
        and not any(item.get(f'{t}_rate') for t in GST_TYPES)
    ]
    if not taxable:
        return items

    tax_rows = frappe.get_all(taxes_doctype,
        filters={'parent': parent, 'gst_tax_type': ['in', GST_TYPES]},
        fields=['gst_tax_type', 'rate', 'base_tax_amount'])
    rates = {t: 0 for t in GST_TYPES}
    totals = {t: 0 for t in GST_TYPES}
    for row in tax_rows:
        rates[row.gst_tax_type] = rates[row.gst_tax_type] or flt(row.rate)
        totals[row.gst_tax_type] += flt(row.base_tax_amount)
    if not any(rates.values()):
        return items

    for item in taxable:
        base = flt(item.get('taxable_value')) or flt(item.get('base_net_amount'))
        for t in GST_TYPES:
            item[f'{t}_rate'] = rates[t]
            item[f'{t}_amount'] = flt(base * rates[t] / 100, 2)

    # Only when every item took the header rate do the item amounts have to
    # match the taxes table exactly.
    if len(taxable) == len(items):
        for t in GST_TYPES:
            diff = flt(totals[t] - sum(item[f'{t}_amount'] for item in items), 2)
            if diff and abs(diff) < 1:
                items[-1][f'{t}_amount'] = flt(items[-1][f'{t}_amount'] + diff, 2)

    return items


def group_item_taxes(items):
    """Items grouped by GST rate with their GST amounts summed: one tax line
    per rate."""
    groups = {}
    for item in items:
        key = tuple(flt(item.get(f'{t}_rate')) for t in GST_TYPES)
        if key not in groups:
            groups[key] = frappe._dict(
                parent=item.get('parent'),
                item_name=item.get('item_name'),
                gst_treatment=item.get('gst_treatment'),
                cgst_rate=key[0], sgst_rate=key[1], igst_rate=key[2],
                cgst_amount=0, sgst_amount=0, igst_amount=0,
            )
        for t in GST_TYPES:
            groups[key][f'{t}_amount'] += flt(item.get(f'{t}_amount'))
    return list(groups.values())


def gst_ledger_suffix(item, voucher_no):
    """The part after '@' in the Sales / Purchase ledger name."""
    if item.get('sgst_rate'):
        return item['sgst_rate'] + item['cgst_rate']
    if item.get('igst_rate'):
        return item['igst_rate']
    if item.get('gst_treatment') in NON_TAXABLE_SUFFIX:
        return NON_TAXABLE_SUFFIX[item['gst_treatment']]

    frappe.log_error(
        title="Tally: item without GST rate",
        message=f"{voucher_no} / {item.get('item_code')}: GST treatment "
                f"'{item.get('gst_treatment')}' but no GST rate on the item or in the taxes table. Sent as Nil Rated.")
    return 'Nil Rated'
