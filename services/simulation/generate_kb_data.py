#!/usr/bin/env python3
"""
Unified Knowledge Base Generator
Generates DynamoDB records + PDF documents + markdown docs in a single run.

Usage:
  DEPLOYMENT_STAGE=prod python3 services/simulation/generate_kb_data.py --region us-east-1
"""

import os, io, json, random, boto3, uuid, argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        if isinstance(o, set): return list(o)
        return super().default(o)

SERVICE_TYPES = [
    {'type': 'OIL_CHANGE', 'cat': 'SCHEDULED', 'cost': (80, 200), 'desc': 'Oil and filter change'},
    {'type': 'TIRE_ROTATION', 'cat': 'SCHEDULED', 'cost': (50, 120), 'desc': 'Tire rotation and balance'},
    {'type': 'BRAKE_PADS', 'cat': 'REPAIR', 'cost': (300, 900), 'desc': 'Brake pad replacement'},
    {'type': 'TRANSMISSION_SERVICE', 'cat': 'SCHEDULED', 'cost': (200, 500), 'desc': 'Transmission fluid change'},
    {'type': 'COOLANT_FLUSH', 'cat': 'SCHEDULED', 'cost': (100, 250), 'desc': 'Coolant system flush'},
    {'type': 'BATTERY_REPLACEMENT', 'cat': 'REPAIR', 'cost': (150, 400), 'desc': 'Battery replacement'},
    {'type': 'STARTER_MOTOR', 'cat': 'REPAIR', 'cost': (400, 800), 'desc': 'Starter motor replacement'},
    {'type': 'ALTERNATOR', 'cat': 'REPAIR', 'cost': (350, 700), 'desc': 'Alternator replacement'},
    {'type': 'FUEL_FILTER', 'cat': 'SCHEDULED', 'cost': (60, 150), 'desc': 'Fuel filter replacement'},
    {'type': 'AIR_FILTER', 'cat': 'SCHEDULED', 'cost': (30, 80), 'desc': 'Air filter replacement'},
    {'type': 'SPARK_PLUGS', 'cat': 'SCHEDULED', 'cost': (100, 300), 'desc': 'Spark plug replacement'},
    {'type': 'DEF_PUMP', 'cat': 'REPAIR', 'cost': (600, 1200), 'desc': 'DEF pump replacement'},
    {'type': 'WHEEL_BEARING', 'cat': 'REPAIR', 'cost': (250, 600), 'desc': 'Wheel bearing replacement'},
    {'type': 'SUSPENSION', 'cat': 'REPAIR', 'cost': (400, 1200), 'desc': 'Suspension repair'},
    {'type': 'AC_COMPRESSOR', 'cat': 'REPAIR', 'cost': (500, 1100), 'desc': 'AC compressor replacement'},
]

PROVIDERS = {
    'Rush Truck Center — Dallas': {'addr': '4455 Irving Blvd, Dallas, TX 75247', 'phone': '(214) 555-0142', 'tax_id': 'TX-RTD-2847'},
    'Penske Truck Leasing — Chicago': {'addr': '2901 S Ashland Ave, Chicago, IL 60608', 'phone': '(312) 555-0198', 'tax_id': 'IL-PTL-9183'},
    'Fleet Service Center Munich': {'addr': '8200 Bayerstr, Munich, BY 80335', 'phone': '+49 89 555-0234', 'tax_id': 'DE-FSC-4521'},
    'Ryder Maintenance — Atlanta': {'addr': '1900 Marietta Blvd NW, Atlanta, GA 30318', 'phone': '(404) 555-0167', 'tax_id': 'GA-RMA-7392'},
    'TravelCenters of America — Phoenix': {'addr': '3200 W Buckeye Rd, Phoenix, AZ 85009', 'phone': '(602) 555-0211', 'tax_id': 'AZ-TCA-5618'},
    'Freightliner of Austin': {'addr': '9501 N IH-35, Austin, TX 78753', 'phone': '(512) 555-0189', 'tax_id': 'TX-FOA-3294'},
}
PROVIDER_NAMES = list(PROVIDERS.keys())

PARTS_CATALOG = {
    # OEM-style commercial-truck parts catalog. Part numbers and brands
    # mirror real-world fleet maintenance vendors (Cummins, Donaldson,
    # Bendix, Delco-Remy, Eaton, Bosch, Fleetguard, Continental, Sanden,
    # Timken, etc.) so generated invoices and work orders read like real
    # documents to a downstream agent doing document understanding /
    # RAG. Keep tuple shape (display_name, part_number, list_price) —
    # generate_invoice_pdf and generate_parts_listing_pdf both depend
    # on it. When you add a service-type here, add the catalog PDF to
    # parts-listings/ via deployment/scripts/backfill_service_invoices.py
    # so the agent's search_kb_docs tool can find it.
    'OIL_CHANGE': [
        ('Synthetic Diesel Engine Oil 15W-40 (6 gal)', 'SH-550054443', 89.99),
        ('Premium Spin-On Oil Filter', 'DON-P550881', 32.50),
        ('Magnetic Drain Plug w/ Gasket', 'FEL-70991', 4.50),
        ('Crankcase Cleaner Additive', 'BAR-12104', 14.00),
    ],
    'TIRE_ROTATION': [
        ('4-Wheel Rotation & Torque Service', 'SVC-TR-001', 0.00),
        ('Wheel Balance Service (per wheel)', 'SVC-WB-001', 18.00),
        ('TPMS Sensor Reset', 'SVC-TPMS-RST', 22.00),
        ('Valve Stem Replacement, ea', 'SCH-20008-50', 14.50),
    ],
    'BRAKE_PADS': [
        ('Front Air Disc Brake Pad Set (Bendix MTM5)', 'BX-MTM5', 349.99),
        ('Rear Air Disc Brake Pad Set (Bendix MTM6)', 'BX-MTM6', 329.99),
        ('Brake Rotor (per axle, ea)', 'BX-RT-2104', 189.00),
        ('Brake Hardware / Hold-Down Kit', 'BX-MK-2104', 42.50),
        ('Caliper Slide Pin & Boot Kit', 'BX-MK-2090', 28.00),
    ],
    'TRANSMISSION_SERVICE': [
        ('Synthetic ATF Fluid (12 qt case)', 'EAT-23710', 174.00),
        ('Transmission Filter & Gasket Kit', 'EAT-K-3236', 96.00),
        ('Transmission Pan Gasket', 'ALL-29548096', 34.50),
        ('Magnetic Pan Drain Plug', 'EAT-56022', 18.50),
        ('Allison Service Refill & Cooler Flush', 'SVC-TRN-FLUSH', 75.00),
    ],
    'COOLANT_FLUSH': [
        ('Heavy-Duty ELC Coolant (2 gal)', 'SH-550046049', 96.00),
        ('Engine Thermostat Assembly', 'CMN-4936026', 58.00),
        ('Upper / Lower Radiator Hose Kit', 'CON-64580', 89.00),
        ('Cooling System Flush Chemical', 'BAR-6740', 24.00),
        ('Pressure Cap, 16 PSI', 'STR-31314', 18.00),
    ],
    'BATTERY_REPLACEMENT': [
        ('Heavy-Duty Commercial Battery 12V Group 31 (Optima)', 'OPT-8052-161', 329.00),
        ('Battery Cable End Kit', 'CON-51301', 18.00),
        ('Terminal Anti-Corrosion Spray', 'CRC-02035', 9.50),
        ('Battery Hold-Down Bracket', 'MOP-56029684AC', 24.00),
    ],
    'STARTER_MOTOR': [
        ('Heavy-Duty Starter Assembly (Delco-Remy 39MT)', 'DR-8200029', 625.00),
        ('Starter Solenoid Assembly', 'DR-1115621', 145.00),
        ('Starter Mounting Bolt Set', 'CON-12345', 14.00),
        ('Battery Cable, Heavy-Duty 2/0 ga', 'CMN-3088584', 42.00),
    ],
    'ALTERNATOR': [
        ('Heavy-Duty Alternator (Delco-Remy 24SI 200A)', 'DR-8600461', 749.00),
        ('Serpentine Belt', 'CON-4060895', 58.00),
        ('Belt Tensioner Assembly', 'CON-49265', 79.00),
        ('Alternator Mounting Bracket', 'CON-64129', 32.00),
    ],
    'FUEL_FILTER': [
        ('Primary Fuel Filter (Fleetguard)', 'FG-FF5421', 48.00),
        ('Secondary Fuel Filter (Fleetguard)', 'FG-FS19763', 46.00),
        ('Fuel/Water Separator Element', 'FG-WF2071', 29.00),
        ('Filter O-Ring & Seal Kit', 'CMN-3963903', 6.50),
    ],
    'DEF_PUMP': [
        ('DEF Pump Assembly (Cummins reman)', 'CMN-4324988RX', 689.00),
        ('DEF Line Kit (supply + return)', 'CMN-5298401', 124.00),
        ('DEF Filter (Fleetguard)', 'FG-CV52001', 42.00),
        ('TerraCair Ultra Pure DEF (2.5 gal)', 'TC-DEF-25', 14.99),
    ],
    # ── Newly added so every serviceType present in DDB has a catalog ──
    'AIR_FILTER': [
        ('Heavy-Duty Engine Air Filter (Donaldson)', 'DON-P181083', 68.00),
        ('Cab Air / HVAC Filter', 'DON-P130601', 24.00),
        ('Air Restriction Indicator', 'DON-X011361', 32.00),
        ('Air Inlet Hose Clamp Set', 'CON-12345-CL', 4.50),
    ],
    'SPARK_PLUGS': [
        ('Iridium Spark Plug Set (8) (Bosch FR8DPP33)', 'BOS-9657', 96.00),
        ('Spark Plug Boot Kit', 'NGK-8333', 32.00),
        ('Coil-on-Plug Boot, ea', 'SMP-UF-525', 42.00),
        ('Anti-Seize Compound 4 oz', 'PER-80078', 9.50),
    ],
    'WHEEL_BEARING': [
        ('Front Wheel Hub & Bearing Assembly (Timken)', 'TIM-HA590070', 389.00),
        ('Rear Wheel Bearing & Race Set (SKF)', 'SKF-GR1116', 187.00),
        ('Wheel Seal (National)', 'NAT-8835', 28.00),
        ('Spindle Nut & Cotter Pin Kit', 'PER-14080', 4.50),
    ],
    'SUSPENSION': [
        ('Front Shock Absorber, ea (KYB Gas-a-Just)', 'KYB-565049', 189.00),
        ('Rear Shock Absorber, ea (KYB Gas-a-Just)', 'KYB-565050', 179.00),
        ('Sway Bar End Link (Moog)', 'MOG-K700537', 52.00),
        ('Sway Bar Bushing Kit (Moog)', 'MOG-K200119', 48.00),
        ('Coil Spring, medium-duty (Moog)', 'MOG-81670', 148.00),
    ],
    'AC_COMPRESSOR': [
        ('A/C Compressor Assembly (Sanden SD7H15)', 'SAN-SD7H15', 548.00),
        ('Receiver Drier', 'FOS-33500', 42.00),
        ('Expansion Valve / TXV', 'FOS-38833', 58.00),
        ('R-1234yf Refrigerant (25 lb cyl)', 'HON-SOL-1234', 128.00),
        ('Compressor Service Valve Set', 'MAS-47675', 32.00),
    ],
    'DIAGNOSTIC_REPAIR': [
        ('Diagnostic Scan & Verification', 'SVC-DIAG-001', 145.00),
        ('ECM Software Update / Reflash', 'CMN-5455701', 89.00),
        ('Sensor Replacement (typical)', 'BOS-SEN-VAR', 185.00),
        ('Wiring Repair / Connector Pin Kit', 'CON-MK-PIN', 38.00),
    ],
    'VSA_VOICE_TRIAGE': [
        ('Voice Agent Triage Inspection', 'SVC-VSA-001', 0.00),
        ('Diagnostic Scan Service', 'SVC-DIAG-001', 95.00),
        ('Triage Summary Report', 'SVC-TRG-MOBILE', 48.00),
    ],
    'VSA_NATIVE_BOOKING': [
        ('Voice Agent Booking Intake', 'SVC-VSA-002', 0.00),
        ('Diagnostic Scan Service', 'SVC-DIAG-001', 95.00),
    ],
    'MAINTENANCE_SERVICE': [
        ('Service Bulletin / TSB Inspection', 'SVC-MTN-001', 0.00),
        ('Replacement Part (TSB)', 'TSB-PARTS-001', 285.00),
        ('Documentation & Compliance Filing', 'SVC-MTN-002', 25.00),
    ],
    'RECALL_SERVICE': [
        ('NHTSA Recall Inspection', 'SVC-RECALL-001', 0.00),
        ('Recall Replacement Part (per TSB)', 'TSB-PARTS-002', 325.00),
        ('Compliance & Filing Service', 'SVC-RECALL-002', 25.00),
    ],
}

TECHNICIANS = ['M. Rodriguez', 'J. Thompson', 'K. Patel', 'S. Williams', 'D. Chen', 'R. Mueller', 'A. Kowalski', 'T. Nakamura']

WARRANTY_COMPONENTS = [
    ('DEF pump', 'DTC P20EE', (600, 1200), '50,000 mi'),
    ('Turbocharger', 'DTC P0299', (1500, 3500), '60,000 mi'),
    ('EGR valve', 'DTC P0401', (400, 900), '80,000 mi'),
    ('Fuel injector', 'DTC P0201', (300, 800), '100,000 mi'),
    ('Brake actuator', 'DTC C0035', (800, 1800), '36,000 mi'),
    ('Battery pack', 'DTC P0A80', (2000, 8000), '100,000 mi'),
    ('Transmission control', 'DTC P0700', (500, 1500), '60,000 mi'),
    ('Catalytic converter', 'DTC P0420', (1000, 3000), '80,000 mi'),
    ('Water pump', 'DTC P0217', (300, 700), '60,000 mi'),
    ('Power steering pump', 'DTC C0545', (400, 900), '50,000 mi'),
]

DTC_CODES = [
    ('P0300', 'Random/Multiple Cylinder Misfire', 'ENGINE', 'HIGH'),
    ('P0171', 'System Too Lean Bank 1', 'FUEL', 'MEDIUM'),
    ('P0420', 'Catalyst System Efficiency Below Threshold', 'EMISSIONS', 'MEDIUM'),
    ('P0562', 'System Voltage Low', 'ELECTRICAL', 'LOW'),
    ('P20EE', 'SCR NOx Catalyst Efficiency Below Threshold', 'EMISSIONS', 'HIGH'),
    ('P0217', 'Engine Overtemperature Condition', 'ENGINE', 'CRITICAL'),
    ('P0520', 'Engine Oil Pressure Sensor Circuit', 'ENGINE', 'HIGH'),
    ('P0700', 'Transmission Control System Malfunction', 'TRANSMISSION', 'HIGH'),
    ('C0035', 'Left Front Wheel Speed Circuit', 'BRAKES', 'HIGH'),
    ('P0401', 'EGR Flow Insufficient Detected', 'EMISSIONS', 'MEDIUM'),
    ('P0299', 'Turbo/Super Charger Underboost', 'ENGINE', 'HIGH'),
    ('U0100', 'Lost Communication With ECM/PCM', 'COMMUNICATION', 'CRITICAL'),
    ('P0A80', 'Replace Hybrid Battery Pack', 'BATTERY', 'CRITICAL'),
    ('B1000', 'ECU Malfunction', 'BODY', 'LOW'),
    ('P0128', 'Coolant Thermostat Below Regulating Temperature', 'ENGINE', 'LOW'),
    ('P0455', 'Evaporative Emission System Leak Detected (Large)', 'EMISSIONS', 'MEDIUM'),
]

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='InvoiceTitle', fontSize=18, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=6))
styles.add(ParagraphStyle(name='SectionHeader', fontSize=11, fontName='Helvetica-Bold', spaceAfter=4, spaceBefore=8))
styles.add(ParagraphStyle(name='SmallText', fontSize=8, fontName='Helvetica', textColor=colors.grey))
styles.add(ParagraphStyle(name='RightAlign', fontSize=10, fontName='Helvetica', alignment=TA_RIGHT))

# ---------------------------------------------------------------------------
# PDF generation functions
# ---------------------------------------------------------------------------

def generate_invoice_pdf(svc):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []

    provider_name = svc.get('provider', 'Fleet Service Center')
    provider_info = PROVIDERS.get(provider_name, {'addr': '123 Main St', 'phone': '(555) 000-0000', 'tax_id': 'XX-000'})
    inv_num = f"INV-{svc['serviceId'].upper()}"
    svc_date = svc.get('serviceDate', '2026-01-01')[:10]

    header_data = [
        [Paragraph(f'<b>{provider_name}</b>', styles['Normal']), '', Paragraph('<b>INVOICE</b>', styles['InvoiceTitle'])],
        [Paragraph(provider_info['addr'], styles['SmallText']), '', Paragraph(f'Invoice #: {inv_num}', styles['RightAlign'])],
        [Paragraph(f"Phone: {provider_info['phone']}", styles['SmallText']), '', Paragraph(f'Date: {svc_date}', styles['RightAlign'])],
        [Paragraph(f"Tax ID: {provider_info['tax_id']}", styles['SmallText']), '', Paragraph(f'Work Order: WO-{random.randint(10000,99999)}', styles['RightAlign'])],
    ]
    t = Table(header_data, colWidths=[3*inch, 1*inch, 3*inch])
    t.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    elements.append(t)
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#0f1b2a')))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph('VEHICLE INFORMATION', styles['SectionHeader']))
    veh_data = [
        ['Vehicle ID:', svc.get('vehicleId', ''), 'VIN:', svc.get('vin', 'N/A')],
        ['Make/Model:', f"{svc.get('make', '')} {svc.get('model', '')}", 'Mileage:', f"{svc.get('mileageAtService', 'N/A')} mi"],
    ]
    t = Table(veh_data, colWidths=[1.2*inch, 2.3*inch, 1*inch, 2.5*inch])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph('SERVICE PERFORMED', styles['SectionHeader']))
    elements.append(Paragraph(f"{svc.get('description', svc.get('serviceType', 'Service'))} — Category: {svc.get('category', 'REPAIR')}", styles['Normal']))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph('PARTS & LABOR', styles['SectionHeader']))
    # Caller may pre-compute the line items, labor rate, and technician —
    # the backfill script (deployment/scripts/backfill_service_invoices.py)
    # passes them via the svc dict so the rendered PDF matches the
    # structured cost breakdown it writes to the service-history row in
    # DDB. When unset (the legacy seed-from-scratch path), we fall back
    # to the original randomised generation so existing callers behave
    # identically.
    pre_line_items = svc.get('lineItems')
    pre_labor_rate = svc.get('laborRate')
    pre_technician = svc.get('technician')
    line_items = [['Item', 'Part #', 'Qty', 'Unit Price', 'Total']]
    parts_total = 0.0
    if pre_line_items:
        for li in pre_line_items:
            qty = int(li.get('qty', 1))
            actual_price = float(li.get('unitPrice', 0))
            total_li = float(li.get('total', round(actual_price * qty, 2)))
            parts_total += total_li
            line_items.append([li.get('name', ''), li.get('partNumber', ''), str(qty), f'${actual_price:.2f}', f'${total_li:.2f}'])
    else:
        parts = PARTS_CATALOG.get(svc.get('serviceType', ''), [('Service Parts', 'P-GEN-001', 50.00)])
        for name, pn, price in parts:
            qty = random.randint(1, 2)
            actual_price = round(price * random.uniform(0.9, 1.1), 2)
            total = round(actual_price * qty, 2)
            parts_total += total
            line_items.append([name, pn, str(qty), f'${actual_price:.2f}', f'${total:.2f}'])

    labor_rate = float(pre_labor_rate) if pre_labor_rate is not None else round(random.uniform(95, 145), 2)
    labor_hours = float(svc.get('laborHours', round(random.uniform(1, 6), 1)))
    labor_total = round(labor_rate * labor_hours, 2)
    line_items.append([f'Labor ({labor_hours} hrs @ ${labor_rate}/hr)', '', '', '', f'${labor_total:.2f}'])

    t = Table(line_items, colWidths=[2.8*inch, 1*inch, 0.5*inch, 1*inch, 1*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f1b2a')), ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey), ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4), ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))

    subtotal = parts_total + labor_total
    tax = round(subtotal * 0.0825, 2)
    warranty_amt = float(svc.get('warrantyCoverage', 0))
    total = round(subtotal + tax - warranty_amt, 2)
    totals_data = [
        ['', '', 'Subtotal:', f'${subtotal:.2f}'],
        ['', '', 'Tax (8.25%):', f'${tax:.2f}'],
    ]
    if warranty_amt > 0:
        totals_data.append(['', '', 'Warranty Credit:', f'-${warranty_amt:.2f}'])
    totals_data.append(['', '', 'TOTAL DUE:', f'${total:.2f}'])
    t = Table(totals_data, colWidths=[2.8*inch, 1.5*inch, 1.2*inch, 1*inch])
    t.setStyle(TableStyle([
        ('FONTNAME', (2,-1), (-1,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ALIGN', (2,0), (-1,-1), 'RIGHT'), ('LINEABOVE', (2,-1), (-1,-1), 1, colors.black),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))

    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph('TECHNICIAN NOTES', styles['SectionHeader']))
    elements.append(Paragraph(svc.get('notes', 'Service completed per manufacturer specifications.'), styles['Normal']))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f'Technician: {pre_technician or random.choice(TECHNICIANS)}', styles['Normal']))
    elements.append(Paragraph(f'Next Service Due: {random.randint(5000,15000)} miles or {random.randint(3,12)} months', styles['SmallText']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f'Warranty Applied: {"YES — Covered under OEM warranty" if svc.get("warrantyApplied") else "NO"}', styles['Normal']))

    doc.build(elements)
    return buf.getvalue()


def generate_work_order_pdf(svc):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []

    wo_num = f"WO-{random.randint(10000,99999)}"
    elements.append(Paragraph('WORK ORDER', styles['InvoiceTitle']))
    elements.append(Paragraph(f'#{wo_num}', styles['InvoiceTitle']))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#d13313')))
    elements.append(Spacer(1, 12))

    info = [
        ['Date Opened:', svc.get('serviceDate', '')[:10], 'Priority:', random.choice(['ROUTINE', 'URGENT', 'EMERGENCY'])],
        ['Vehicle:', f"{svc.get('vehicleId', '')} — {svc.get('make', '')} {svc.get('model', '')}", 'VIN:', svc.get('vin', '')],
        ['Mileage:', f"{svc.get('mileageAtService', '')} mi", 'Assigned To:', random.choice(TECHNICIANS)],
        ['Provider:', svc.get('provider', ''), 'Status:', svc.get('status', 'COMPLETED')],
    ]
    t = Table(info, colWidths=[1.2*inch, 2.5*inch, 1*inch, 2.3*inch])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f5f5')), ('BOX', (0,0), (-1,-1), 1, colors.grey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph('WORK DESCRIPTION', styles['SectionHeader']))
    elements.append(Paragraph(f"Service Type: {svc.get('serviceType', 'GENERAL')}", styles['Normal']))
    elements.append(Paragraph(f"Description: {svc.get('description', 'General service')}", styles['Normal']))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph('INSPECTION CHECKLIST', styles['SectionHeader']))
    checks = [
        ['☑ Fluid levels checked', '☑ Tire pressure verified', '☑ Brake inspection'],
        ['☑ Battery test', '☑ Belt inspection', '☑ Light check'],
        ['☑ Wiper blades', '☑ Air filter', '☑ Exhaust system'],
    ]
    t = Table(checks, colWidths=[2.3*inch, 2.3*inch, 2.3*inch])
    t.setStyle(TableStyle([('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 3)]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(f"Labor Hours: {svc.get('laborHours', '2.0')}", styles['Normal']))
    elements.append(Paragraph(f"Total Cost: ${svc.get('cost', '0')}", styles['Normal']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Technician Signature: _________________ Date: {svc.get('serviceDate', '')[:10]}", styles['Normal']))
    elements.append(Paragraph("Customer Approval: _________________ Date: _________", styles['Normal']))

    doc.build(elements)
    return buf.getvalue()


def generate_parts_listing_pdf(service_type):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []

    elements.append(Paragraph('PARTS LISTING & PRICING', styles['InvoiceTitle']))
    elements.append(Paragraph(f'Service Category: {service_type.replace("_", " ").title()}', styles['SectionHeader']))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    elements.append(Spacer(1, 12))

    parts = PARTS_CATALOG.get(service_type, [])
    if not parts:
        elements.append(Paragraph('No parts catalog available for this service type.', styles['Normal']))
    else:
        data = [['Part Name', 'Part Number', 'List Price', 'Fleet Price', 'In Stock']]
        for name, pn, price in parts:
            fleet_price = round(price * 0.85, 2)
            stock = random.choice(['Yes', 'Yes', 'Yes', 'Yes', 'Limited', 'Order'])
            data.append([name, pn, f'${price:.2f}', f'${fleet_price:.2f}', stock])
        t = Table(data, colWidths=[2.5*inch, 1.2*inch, 1*inch, 1*inch, 0.8*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f1b2a')), ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey), ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        elements.append(t)

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f'Catalog Date: {datetime.now().strftime("%Y-%m-%d")}', styles['SmallText']))
    elements.append(Paragraph('Fleet discount: 15% off list price. Prices subject to change.', styles['SmallText']))

    doc.build(elements)
    return buf.getvalue()


def generate_warranty_claim_pdf(claim):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []

    elements.append(Paragraph('WARRANTY CLAIM', styles['InvoiceTitle']))
    elements.append(Paragraph(f"Claim #{claim['claimId']}", styles['InvoiceTitle']))

    status_color = {'PAID': '#00802f', 'OPEN': '#d4a017', 'DENIED': '#d13313', 'UNDER_REVIEW': '#0972d3'}.get(claim.get('status', ''), '#000')
    elements.append(Paragraph(f'<font color="{status_color}"><b>Status: {claim.get("status", "UNKNOWN")}</b></font>', styles['Normal']))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor(status_color)))
    elements.append(Spacer(1, 12))

    info = [
        ['Filed Date:', claim.get('filedDate', ''), 'Resolved:', claim.get('resolvedDate', 'Pending')],
        ['Vehicle:', f"{claim.get('vehicleId', '')} — {claim.get('make', '')}", 'VIN:', claim.get('vin', '')],
        ['Component:', claim.get('component', ''), 'Failure Code:', claim.get('failureCode', '')],
        ['Mileage:', f"{claim.get('mileageAtFailure', '')} mi", 'Warranty Limit:', claim.get('warrantyLimit', '')],
    ]
    t = Table(info, colWidths=[1.2*inch, 2.5*inch, 1.2*inch, 2.1*inch])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('BOX', (0,0), (-1,-1), 1, colors.grey), ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f5f5')),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph('FINANCIAL SUMMARY', styles['SectionHeader']))
    fin = [
        ['Claim Amount:', f"${claim.get('claimAmount', 0)}"],
        ['Paid Amount:', f"${claim.get('paidAmount', 0)}"],
        ['Days Remaining:', str(claim.get('daysRemaining', ''))],
        ['Confidence Score:', f"{claim.get('confidence', '')}%"],
    ]
    t = Table(fin, colWidths=[2*inch, 2*inch])
    t.setStyle(TableStyle([('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph('EVIDENCE SUMMARY', styles['SectionHeader']))
    elements.append(Paragraph(claim.get('evidenceSummary', 'No evidence available.'), styles['Normal']))

    doc.build(elements)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# DynamoDB data generation
# ---------------------------------------------------------------------------

def load_vehicles(dynamodb):
    table = dynamodb.Table(f'cms-{STAGE}-storage-vehicles')
    items, resp = [], table.scan()
    items.extend(resp.get('Items', []))
    while 'LastEvaluatedKey' in resp:
        resp = table.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
        items.extend(resp.get('Items', []))
    print(f"✅ Loaded {len(items)} vehicles from DynamoDB")
    return items


def generate_service_history(vehicles, days=730):
    records = []
    for v in vehicles:
        for _ in range(random.randint(8, 20)):
            svc = random.choice(SERVICE_TYPES)
            cost = random.randint(*svc['cost'])
            warranty = random.random() < 0.15
            dt = datetime.now(timezone.utc) - timedelta(days=random.randint(1, days))
            mileage = int(float(v.get('currentMileage', v.get('annualMiles', random.randint(10000, 80000))))) - random.randint(0, 20000)
            records.append({
                'serviceId': str(uuid.uuid4())[:8],
                'vehicleId': v['vehicleId'],
                'vin': v.get('vin', ''),
                'make': v.get('make', ''),
                'model': v.get('model', ''),
                'serviceType': svc['type'],
                'category': svc['cat'],
                'description': svc['desc'],
                'cost': Decimal(str(cost)),
                'laborHours': Decimal(str(round(random.uniform(0.5, 8.0), 1))),
                'provider': random.choice(PROVIDER_NAMES),
                'providerType': 'Service Center',
                'serviceDate': dt.isoformat(),
                'mileageAtService': Decimal(str(max(1000, mileage))),
                'status': 'COMPLETED',
                'warrantyApplied': warranty,
                'warrantyCoverage': Decimal(str(cost if warranty else 0)),
                'notes': f"{'Warranty claim filed — ' if warranty else ''}Repair completed — {svc['desc']}",
            })
    print(f"   Generated {len(records)} service history records")
    return records


def generate_warranty_claims(vehicles):
    claims = []
    statuses = ['OPEN', 'PAID', 'PAID', 'PAID', 'PAID', 'DENIED', 'UNDER_REVIEW']
    for v in vehicles:
        if random.random() < 0.65:
            for _ in range(random.randint(1, 4)):
                comp, dtc, cost_range, limit = random.choice(WARRANTY_COMPONENTS)
                amount = random.randint(*cost_range)
                status = random.choice(statuses)
                filed = datetime.now(timezone.utc) - timedelta(days=random.randint(10, 400))
                claims.append({
                    'claimId': f"CLM-{filed.year}-{random.randint(100,9999)}",
                    'vehicleId': v['vehicleId'],
                    'vin': v.get('vin', ''),
                    'make': v.get('make', ''),
                    'oem': v.get('make', ''),
                    'component': comp,
                    'failureCode': dtc,
                    'claimAmount': Decimal(str(amount)),
                    'paidAmount': Decimal(str(amount if status == 'PAID' else 0)),
                    'status': status,
                    'filedDate': filed.strftime('%Y-%m-%d'),
                    'resolvedDate': (filed + timedelta(days=random.randint(14, 60))).strftime('%Y-%m-%d') if status in ('PAID', 'DENIED') else '',
                    'warrantyLimit': limit,
                    'mileageAtFailure': Decimal(str(random.randint(12000, 48000))),
                    'daysRemaining': Decimal(str(random.randint(30, 500))),
                    'confidence': Decimal(str(random.randint(55, 99))),
                    'evidenceSummary': f"Telemetry data shows {dtc} triggered at {random.randint(12000,48000)} miles. Component failure pattern confirmed via predictive maintenance model.",
                })
    print(f"   Generated {len(claims)} warranty claims")
    return claims


def generate_dtc_history(vehicles, days=365):
    records = []
    seen_keys = set()
    for v in vehicles:
        for _ in range(random.randint(5, 20)):
            code, desc, system, severity = random.choice(DTC_CODES)
            ts = datetime.now(timezone.utc) - timedelta(days=random.randint(1, days), seconds=random.randint(0, 86400))
            ts_millis = int(ts.timestamp() * 1000) + random.randint(0, 999)
            key = (v['vehicleId'], ts_millis)
            while key in seen_keys:
                ts_millis += 1
                key = (v['vehicleId'], ts_millis)
            seen_keys.add(key)
            records.append({
                'dtcId': str(uuid.uuid4())[:8],
                'vehicleId': v['vehicleId'],
                'vin': v.get('vin', ''),
                'code': code,
                'description': desc,
                'system': system,
                'severity': severity,
                'timestamp': Decimal(str(ts_millis)),
                'mileage': Decimal(str(random.randint(8000, 90000))),
                'status': random.choice(['ACTIVE', 'CLEARED', 'CLEARED', 'CLEARED', 'CLEARED']),
                'serviceRequired': random.random() < 0.4,
                'relatedServiceId': '',
                'clearedDate': '',
            })
    print(f"   Generated {len(records)} DTC records")
    return records


def batch_write(dynamodb, table_name, items):
    table = dynamodb.Table(table_name)
    desc = dynamodb.meta.client.describe_table(TableName=table_name)
    keys = [k['AttributeName'] for k in desc['Table']['KeySchema']]
    seen = set()
    deduped = []
    for item in items:
        key = tuple(str(item.get(k, '')) for k in keys)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    with table.batch_writer() as batch:
        for item in deduped:
            batch.put_item(Item=item)
    print(f"   Wrote {len(deduped)} items to {table_name}")


# ---------------------------------------------------------------------------
# Fleet context markdown docs
# ---------------------------------------------------------------------------

def upload_fleet_context(s3, bucket, vehicles):
    fleets = {}
    for v in vehicles:
        fleets.setdefault(v.get('fleetId', 'UNKNOWN'), []).append(v)

    comp = f'# Fleet Composition\n\nTotal vehicles: {len(vehicles)}\n\n'
    for fid, vlist in sorted(fleets.items()):
        makes = {}
        for v in vlist:
            m = v.get('make', 'Unknown')
            makes[m] = makes.get(m, 0) + 1
        make_str = ', '.join(f'{c}x {m}' for m, c in makes.items())
        comp += f'## {fid} ({len(vlist)} vehicles)\n{make_str}\n\n'

    docs = {
        'fleet-context/fleet-composition.md': comp,
        'fleet-context/fleet-kpis-and-targets.md': """# Fleet KPIs and Targets

| Metric | Target | Current |
|--------|--------|---------|
| Utilization | 82% | 78% |
| Cost per mile | $0.70 | $0.72 |
| Maintenance ratio | <30% | 34% |
| Safety score | >85 | 81 |
| Recall compliance | 100% in 30 days | 71% |
| Warranty recovery | >90% eligible filed | 85% |
| Fleet health score | >80 | 74 |
| Average driver score | >90 | 87 |
""",
        'fleet-context/agent-glossary.md': """# Virtual Fleet Operator Glossary

- **Fleet Health Score**: Composite 0-100 score. Weights: Recall 30%, Utilization 25%, Cost 25%, Maintenance 20%
- **Cross-domain cascade**: When an event in one domain impacts others (e.g., recall grounds vehicles, reducing utilization, increasing costs)
- **TCO**: Total Cost of Ownership — all costs: fuel, maintenance, insurance, depreciation, charging
- **Cost per mile**: Total fleet cost / total miles driven
- **Utilization**: Percentage of vehicles actively in use vs. available fleet
- **DTC**: Diagnostic Trouble Code — standardized codes from vehicle ECU
- **NHTSA**: National Highway Traffic Safety Administration — issues recalls
- **TSB**: Technical Service Bulletin — OEM advisory, not a mandatory recall
- **DEF**: Diesel Exhaust Fluid — used in SCR systems for emissions compliance
- **SoH**: State of Health — battery degradation metric for EVs (100% = new)
- **FMCSA**: Federal Motor Carrier Safety Administration — regulates commercial vehicles
- **PM**: Preventive Maintenance — scheduled maintenance to prevent failures
- **CM**: Corrective Maintenance — unplanned repairs after failure
""",
        'fleet-operations/cross-domain-escalation-playbook.md': """# Cross-Domain Escalation Playbook

## Rule 1: Safety Always Wins
Recall grounding and safety events ALWAYS take priority over cost optimization or utilization targets. Never recommend keeping a recalled vehicle in service to improve utilization numbers.

## Rule 2: Recall Cascade
When a recall grounds vehicles:
1. Recall Agent: identify affected vehicles by VIN range, check telemetry for failure indicators
2. Rebalancing Agent: find surplus vehicles in other regions to cover the gap
3. Cost Agent: estimate total financial impact (service cost + revenue loss + transfer cost)
4. Warranty Agent: check if repairs are warranty-eligible, file claims
5. Supervisor: synthesize into unified action plan with net cost impact

## Rule 3: Cost Spike Investigation
When cost per mile exceeds 1.5x fleet average:
1. Cost Agent: identify the vehicle and cost category (fuel, maintenance, insurance)
2. Check maintenance history for deferred repairs causing cascading failures
3. Check DTC history for recurring codes indicating underlying issues
4. Check telemetry for driving behavior changes (harsh braking, speeding, idling)
5. Recommend: repair, retrain driver, reassign route, or retire vehicle

## Rule 4: Cross-Domain Approval
Any action plan spanning 2+ domains requires human approval before execution. The operator sees the full plan with estimated costs and impacts before approving.

## Rule 5: Warranty Recovery
When a repair is completed, always check warranty eligibility before closing the service ticket. File claims within 30 days of service completion. Track claim status and follow up on UNDER_REVIEW claims after 14 days.

## Rule 6: Vehicle Lifecycle
When maintenance cost trajectory exceeds depreciation curve (TCO crossover), flag for replacement evaluation. Consider: residual value, maintenance forecast, fleet demand, replacement lead time.
""",
        'fleet-operations/fleet-operations-handbook.md': """# Fleet Operations Handbook

## Daily Operations
1. Review Fleet Command Center daily briefing at start of shift
2. Address Critical and High priority items in the action queue first
3. Review agent recommendations — approve, reject, or modify
4. Monitor real-time utilization across all regions

## Vehicle Dispatch
- Minimum 82% utilization target per region
- EV vehicles: ensure sufficient charge for assigned route (>80% SoC at departure)
- Heavy duty: check hours of service compliance before dispatch
- Service fleet: prioritize by customer SLA urgency

## Incident Response
1. Safety event (collision, rollover): immediate driver contact, dispatch emergency services
2. Breakdown: check DTC codes remotely, dispatch roadside assistance or tow
3. Recall notification: ground affected vehicles within 24 hours, schedule service
4. Warranty expiration: file all pending claims before expiration date

## Cost Management
- Fuel: use preferred fuel card network, monitor price per gallon by region
- Maintenance: follow PM schedule strictly — deferred maintenance costs 3x more
- Insurance: annual review, bundle discounts for clean safety records
- EV charging: off-peak charging saves 40% on electricity costs

## Reporting
- Daily: Fleet Command Center briefing (auto-generated)
- Weekly: utilization and cost summary by region
- Monthly: full TCO report with trend analysis
- Quarterly: vehicle lifecycle review, fleet composition optimization
""",
    }
    for key, body in docs.items():
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType='text/markdown')
    print(f"   Uploaded {len(docs)} fleet context docs to s3://{bucket}")
    return len(docs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Generate KB data, PDFs, and service records')
    parser.add_argument('--region', default=REGION)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--max-invoices', type=int, default=200)
    args = parser.parse_args()

    region = args.region
    session = boto3.Session(profile_name=args.profile, region_name=region)
    dynamodb = session.resource('dynamodb', region_name=region)
    s3 = session.client('s3', region_name=region)
    # Bucket name suffixed with -{region}-{account} per spec
    # `2026-06-04-cms-vfo-kb-bucket-region-suffix`. The bucket is now
    # CDK-owned (declared in bedrock_agents_stack.py) — the lazy
    # create_bucket call below remains as a fresh-region bootstrap
    # safety net for operators who run the seed before the CDK deploy.
    sts = session.client('sts')
    account = os.environ.get('AWS_ACCOUNT_ID') or sts.get_caller_identity()['Account']
    bucket = os.environ.get('ADP_KB_BUCKET', f'cms-{STAGE}-vfo-knowledge-base-{region}-{account}')

    print(f"🚀 Unified KB & PDF Generator")
    print(f"   Region: {region} | Stage: {STAGE} | Bucket: {bucket}\n")

    # Ensure bucket exists
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        try:
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={'LocationConstraint': region})
            print(f"   Created bucket: {bucket}")
        except Exception as e:
            print(f"   Bucket: {e}")

    # Clean up previous run data
    print("🧹 Cleaning up previous data...")
    for table_name in [f'cms-{STAGE}-storage-service-history', f'cms-{STAGE}-storage-warranty-claims', f'cms-{STAGE}-storage-dtc-history']:
        try:
            table = dynamodb.Table(table_name)
            desc = dynamodb.meta.client.describe_table(TableName=table_name)
            keys = [k['AttributeName'] for k in desc['Table']['KeySchema']]
            scan = table.scan(ProjectionExpression=', '.join(keys))
            items = scan.get('Items', [])
            while 'LastEvaluatedKey' in scan:
                scan = table.scan(ProjectionExpression=', '.join(keys), ExclusiveStartKey=scan['LastEvaluatedKey'])
                items.extend(scan.get('Items', []))
            if items:
                with table.batch_writer() as batch:
                    for item in items:
                        batch.delete_item(Key={k: item[k] for k in keys})
                print(f"   Deleted {len(items)} items from {table_name}")
            else:
                print(f"   {table_name}: empty")
        except Exception as e:
            print(f"   {table_name}: {e}")

    # Clear S3 bucket
    try:
        paginator = s3.get_paginator('list_objects_v2')
        del_count = 0
        for page in paginator.paginate(Bucket=bucket):
            objects = [{'Key': o['Key']} for o in page.get('Contents', [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={'Objects': objects})
                del_count += len(objects)
        if del_count:
            print(f"   Deleted {del_count} objects from s3://{bucket}")
    except Exception as e:
        print(f"   S3 cleanup: {e}")

    # 1. Load vehicles
    vehicles = load_vehicles(dynamodb)
    if not vehicles:
        print("❌ No vehicles found. Run the main data injector first.")
        return

    # 2. Service history → DDB → invoice PDFs + work order PDFs → S3
    print("\n🔧 Generating service history...")
    service_records = generate_service_history(vehicles)
    batch_write(dynamodb, f'cms-{STAGE}-storage-service-history', service_records)

    print("\n📋 Generating invoice & work-order PDFs...")
    inv_count = 0
    wo_count = 0
    records_to_pdf = service_records[:args.max_invoices]
    for i, svc in enumerate(records_to_pdf):
        pdf = generate_invoice_pdf(svc)
        key = f"service-invoices/INV-{svc['serviceId']}_{svc['vehicleId']}_{svc['serviceType'].lower()}.pdf"
        s3.put_object(Bucket=bucket, Key=key, Body=pdf, ContentType='application/pdf')
        inv_count += 1
        if i % 3 == 2:
            pdf = generate_work_order_pdf(svc)
            key = f"work-orders/WO-{svc['serviceId']}_{svc['vehicleId']}_{svc['serviceType'].lower()}.pdf"
            s3.put_object(Bucket=bucket, Key=key, Body=pdf, ContentType='application/pdf')
            wo_count += 1
        if inv_count % 50 == 0:
            print(f"   {inv_count} invoices, {wo_count} work orders...")
    print(f"   ✅ {inv_count} invoice PDFs, {wo_count} work order PDFs")

    # 3. Warranty claims → DDB → claim PDFs → S3
    print("\n📋 Generating warranty claims...")
    warranty_claims = generate_warranty_claims(vehicles)
    batch_write(dynamodb, f'cms-{STAGE}-storage-warranty-claims', warranty_claims)

    print("   Generating warranty claim PDFs...")
    wc_count = 0
    for claim in warranty_claims:
        pdf = generate_warranty_claim_pdf(claim)
        key = f"warranty-claims/{claim['claimId']}_{claim['vehicleId']}.pdf"
        s3.put_object(Bucket=bucket, Key=key, Body=pdf, ContentType='application/pdf')
        wc_count += 1
    print(f"   ✅ {wc_count} warranty claim PDFs")

    # 4. DTC history → DDB
    print("\n🔍 Generating DTC history...")
    dtc_records = generate_dtc_history(vehicles)
    batch_write(dynamodb, f'cms-{STAGE}-storage-dtc-history', dtc_records)

    # 5. Parts listing PDFs → S3
    print("\n📦 Generating parts listing PDFs...")
    pl_count = 0
    for svc_type in PARTS_CATALOG:
        pdf = generate_parts_listing_pdf(svc_type)
        key = f"parts-listings/{svc_type.lower()}_parts_catalog.pdf"
        s3.put_object(Bucket=bucket, Key=key, Body=pdf, ContentType='application/pdf')
        pl_count += 1
    print(f"   ✅ {pl_count} parts listing PDFs")

    # 6. Fleet context markdowns → S3
    print("\n📚 Generating fleet context docs...")
    md_count = upload_fleet_context(s3, bucket, vehicles)

    # Summary
    total_docs = inv_count + wo_count + wc_count + pl_count + md_count
    print(f"\n🎉 Complete!")
    print(f"   • {len(service_records)} service history records → DDB")
    print(f"   • {len(warranty_claims)} warranty claims → DDB")
    print(f"   • {len(dtc_records)} DTC records → DDB")
    print(f"   • {inv_count} invoice PDFs → S3")
    print(f"   • {wo_count} work order PDFs → S3")
    print(f"   • {wc_count} warranty claim PDFs → S3")
    print(f"   • {pl_count} parts listing PDFs → S3")
    print(f"   • {md_count} fleet context docs → S3")
    print(f"   Total: {total_docs} documents in s3://{bucket}")


if __name__ == '__main__':
    main()
