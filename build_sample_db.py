"""Builds rentdrive_sample.db: a SQLite copy of the car_rental_db structure
filled with made-up data. Safe to publish (no real customers).
Run:  python build_sample_db.py
"""
import random, sqlite3, os
from datetime import date, datetime, timedelta

random.seed(42)
TODAY = date(2026, 10, 5)
DB = "rentdrive_sample.db"
if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)
cur = con.cursor()

cur.executescript("""
PRAGMA foreign_keys = ON;
CREATE TABLE branch (
  branch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  branch_name TEXT NOT NULL, city TEXT NOT NULL, phone TEXT NOT NULL,
  UNIQUE (branch_name, city));
CREATE TABLE customer (
  customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, licence_number TEXT NOT NULL UNIQUE,
  licence_expiry DATE NOT NULL, phone TEXT NOT NULL, email TEXT);
CREATE TABLE vehiclecategory (
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_name TEXT NOT NULL UNIQUE,
  seating_capacity INTEGER NOT NULL CHECK (seating_capacity > 0));
CREATE TABLE rateplan (
  rate_plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES vehiclecategory(category_id),
  daily_rate DECIMAL(10,2) NOT NULL CHECK (daily_rate >= 0),
  per_km_rate DECIMAL(10,2) NOT NULL CHECK (per_km_rate >= 0),
  effective_from DATE NOT NULL);
CREATE TABLE vehicle (
  vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
  reg_number TEXT NOT NULL UNIQUE,
  category_id INTEGER NOT NULL REFERENCES vehiclecategory(category_id),
  branch_id INTEGER NOT NULL REFERENCES branch(branch_id),
  model TEXT NOT NULL,
  year_made INTEGER NOT NULL CHECK (year_made BETWEEN 1990 AND 2100),
  status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE','RENTED','MAINTENANCE')));
CREATE TABLE reservation (
  reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customer(customer_id),
  vehicle_id INTEGER NOT NULL REFERENCES vehicle(vehicle_id),
  branch_id INTEGER NOT NULL REFERENCES branch(branch_id),
  start_date DATE NOT NULL, end_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'BOOKED' CHECK (status IN ('BOOKED','CANCELLED','COMPLETED')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CHECK (end_date > start_date));
CREATE TABLE rental (
  rental_id INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id INTEGER NOT NULL UNIQUE REFERENCES reservation(reservation_id),
  pickup_date DATETIME NOT NULL, return_date DATETIME,
  pickup_odometer INTEGER NOT NULL CHECK (pickup_odometer >= 0),
  return_odometer INTEGER,
  pickup_branch_id INTEGER NOT NULL REFERENCES branch(branch_id),
  return_branch_id INTEGER REFERENCES branch(branch_id),
  CHECK (return_date IS NULL OR return_date > pickup_date),
  CHECK (return_odometer IS NULL OR return_odometer >= pickup_odometer));
CREATE TABLE damage (
  damage_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rental_id INTEGER NOT NULL REFERENCES rental(rental_id),
  description TEXT NOT NULL,
  repair_cost DECIMAL(10,2) NOT NULL CHECK (repair_cost >= 0),
  reported_date DATE NOT NULL);
CREATE TABLE fuelreading (
  fuel_reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rental_id INTEGER NOT NULL REFERENCES rental(rental_id),
  reading_type TEXT NOT NULL CHECK (reading_type IN ('PICKUP','RETURN')),
  fuel_level_pct INTEGER NOT NULL CHECK (fuel_level_pct BETWEEN 0 AND 100),
  reading_date DATETIME NOT NULL);
CREATE TABLE inspection (
  inspection_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rental_id INTEGER NOT NULL REFERENCES rental(rental_id),
  inspection_type TEXT NOT NULL CHECK (inspection_type IN ('PICKUP','RETURN')),
  inspection_date DATETIME NOT NULL,
  condition_rating TEXT NOT NULL CHECK (condition_rating IN ('EXCELLENT','GOOD','FAIR','POOR')),
  notes TEXT);
CREATE TABLE invoice (
  invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rental_id INTEGER NOT NULL UNIQUE REFERENCES rental(rental_id),
  rental_charge DECIMAL(10,2) NOT NULL CHECK (rental_charge >= 0),
  damage_charge DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (damage_charge >= 0),
  fuel_charge DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (fuel_charge >= 0),
  total_amount DECIMAL(10,2) NOT NULL CHECK (total_amount >= 0),
  invoice_date DATE NOT NULL);
CREATE TABLE payment (
  payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoice(invoice_id),
  amount_paid DECIMAL(10,2) NOT NULL CHECK (amount_paid >= 0),
  payment_date DATE NOT NULL,
  payment_mode TEXT NOT NULL CHECK (payment_mode IN ('CASH','CARD','UPI','NETBANKING')));
CREATE VIEW vw_fleet_status AS
  SELECT v.vehicle_id, v.reg_number, vc.category_name, b.branch_name, v.status
  FROM vehicle v JOIN vehiclecategory vc ON v.category_id = vc.category_id
  JOIN branch b ON v.branch_id = b.branch_id;
""")

# ---- reference data
branches = [("Madhapur Hub","Hyderabad"),("Airport Terminal","Hyderabad"),("Koramangala","Bengaluru"),
            ("Whitefield","Bengaluru"),("Bandra","Mumbai"),("Connaught Place","Delhi"),
            ("T Nagar","Chennai"),("Park Street","Kolkata")]
for i,(n,c) in enumerate(branches):
    cur.execute("INSERT INTO branch(branch_name,city,phone) VALUES (?,?,?)",(n,c,f"98{random.randint(10000000,99999999)}"))

cats = [("Hatchback",5,["Maruti Swift","Hyundai i20","Tata Altroz"],1100,9),
        ("Sedan",5,["Honda City","Hyundai Verna","Skoda Slavia"],1800,12),
        ("SUV",7,["Mahindra XUV700","Tata Safari","Toyota Fortuner"],3000,16),
        ("Compact SUV",5,["Hyundai Creta","Kia Seltos","Tata Nexon"],2200,13),
        ("MUV",7,["Maruti Ertiga","Toyota Innova"],2400,14),
        ("Luxury Sedan",5,["BMW 3 Series","Mercedes C-Class"],6500,30),
        ("Luxury SUV",5,["Audi Q5","BMW X3"],8000,35),
        ("Convertible",4,["Mini Cooper Convertible"],7000,32),
        ("Electric",5,["Tata Nexon EV","MG ZS EV"],2600,10),
        ("Van",12,["Force Traveller"],3500,18),
        ("Pickup",5,["Isuzu D-Max"],2800,15),
        ("Mini Hatch",4,["Tata Tiago","Maruti Alto"],800,7)]
for name,seats,_,rate,km in cats:
    cur.execute("INSERT INTO vehiclecategory(category_name,seating_capacity) VALUES (?,?)",(name,seats))
rates = {}
for cid,(name,seats,_,rate,km) in enumerate(cats, start=1):
    cur.execute("INSERT INTO rateplan(category_id,daily_rate,per_km_rate,effective_from) VALUES (?,?,?,?)",(cid,rate,km,"2025-01-01"))
    rates[cid] = (rate,km)

first = ["Aarav","Vivaan","Aditya","Sai","Arjun","Rohan","Karthik","Ishaan","Rahul","Vikram","Ananya","Diya","Priya","Sneha","Kavya","Meera","Neha","Pooja","Riya","Lakshmi","Harsha","Teja","Varun","Nikhil","Manoj"]
last = ["Reddy","Sharma","Patel","Nair","Iyer","Khan","Singh","Gupta","Rao","Das","Menon","Joshi","Verma","Naidu","Kulkarni"]
names=set()
while len(names)<70:
    names.add(f"{random.choice(first)} {random.choice(last)}")
for i,n in enumerate(sorted(names), start=1):
    if i<=6: exp = TODAY + timedelta(days=random.randint(15,70))   # expiring soon
    else:    exp = date(random.randint(2027,2032),random.randint(1,12),random.randint(1,28))
    cur.execute("INSERT INTO customer(name,licence_number,licence_expiry,phone,email) VALUES (?,?,?,?,?)",
        (n,f"TS{random.randint(10,99)}{2010+random.randint(0,12)}{random.randint(1000000,9999999)}",exp.isoformat(),
         f"9{random.randint(100000000,999999999)}", f"{n.lower().replace(' ','.')}@example.com" if random.random()<0.9 else None))
cust_exp = {r[0]:date.fromisoformat(r[1]) for r in cur.execute("SELECT customer_id,licence_expiry FROM customer")}

# vehicles
veh=[]
vid=0
for cid,(name,seats,models,rate,km) in enumerate(cats, start=1):
    for k in range(10):
        vid+=1
        reg=f"TS{random.randint(1,38):02d}{random.choice('ABCDEFGH')}{random.choice('KLMNPQRS')}{random.randint(1000,9999)}"
        cur.execute("INSERT INTO vehicle(reg_number,category_id,branch_id,model,year_made,status) VALUES (?,?,?,?,?,'AVAILABLE')",
            (reg,cid,random.randint(1,len(branches)),random.choice(models),random.randint(2020,2026)))
        veh.append(vid)
vcat = {r[0]:r[1] for r in cur.execute("SELECT vehicle_id,category_id FROM vehicle")}
vodo = {v:random.randint(2000,40000) for v in veh}

damages = ["Scratch on rear bumper","Dent on left door","Cracked windshield","Broken side mirror","Flat tyre damaged beyond repair","Seat upholstery torn","Headlight cracked","Paint scrape on front fender"]
notes_ok = ["No issues","Clean and ready","Minor wear only","All accessories present"]
pay_modes = ["CASH","CARD","UPI","NETBANKING"]

def add_rental(res_id, v, branch, s, e, status):
    """status: 'done' | 'open'"""
    pick = datetime(s.year,s.month,s.day,random.randint(8,12),0)
    odo = vodo[v]
    cur.execute("INSERT INTO rental(reservation_id,pickup_date,pickup_odometer,pickup_branch_id) VALUES (?,?,?,?)",
                (res_id,pick.isoformat(sep=' '),odo,branch))
    rid = cur.lastrowid
    pf = random.choice([100,100,90,80])
    cur.execute("INSERT INTO fuelreading(rental_id,reading_type,fuel_level_pct,reading_date) VALUES (?,?,?,?)",(rid,'PICKUP',pf,pick.isoformat(sep=' ')))
    cur.execute("INSERT INTO inspection(rental_id,inspection_type,inspection_date,condition_rating,notes) VALUES (?,?,?,?,?)",
                (rid,'PICKUP',pick.isoformat(sep=' '),random.choice(['EXCELLENT','EXCELLENT','GOOD']),random.choice(notes_ok)))
    if status=='open':
        return rid
    days=(e-s).days
    ret = datetime(e.year,e.month,e.day,random.randint(9,19),0)
    dist = days*random.randint(60,220)
    rb = branch if random.random()<0.8 else random.randint(1,len(branches))
    cur.execute("UPDATE rental SET return_date=?,return_odometer=?,return_branch_id=? WHERE rental_id=?",
                (ret.isoformat(sep=' '),odo+dist,rb,rid))
    vodo[v]=odo+dist
    rf = max(5,pf-random.randint(10,70))
    cur.execute("INSERT INTO fuelreading(rental_id,reading_type,fuel_level_pct,reading_date) VALUES (?,?,?,?)",(rid,'RETURN',rf,ret.isoformat(sep=' ')))
    dmg=0.0
    cond=random.choice(['EXCELLENT','GOOD','GOOD','FAIR'])
    if random.random()<0.09:
        dmg=float(random.choice([2500,4000,7500,12000,18000]))
        cur.execute("INSERT INTO damage(rental_id,description,repair_cost,reported_date) VALUES (?,?,?,?)",
                    (rid,random.choice(damages),dmg,e.isoformat()))
        cond=random.choice(['FAIR','POOR'])
    cur.execute("INSERT INTO inspection(rental_id,inspection_type,inspection_date,condition_rating,notes) VALUES (?,?,?,?,?)",
                (rid,'RETURN',ret.isoformat(sep=' '),cond,random.choice(notes_ok) if dmg==0 else "Damage reported"))
    daily,perkm = rates[vcat[v]]
    rc = round(days*daily + dist*perkm*0.2,2)
    fc = round(max(0,(pf-rf)-30)*25.0,2)
    total = round(rc+dmg+fc,2)
    inv_date = e + timedelta(days=1)
    cur.execute("INSERT INTO invoice(rental_id,rental_charge,damage_charge,fuel_charge,total_amount,invoice_date) VALUES (?,?,?,?,?,?)",
                (rid,rc,dmg,fc,total,inv_date.isoformat()))
    iid = cur.lastrowid
    r=random.random()
    if r<0.82:
        cur.execute("INSERT INTO payment(invoice_id,amount_paid,payment_date,payment_mode) VALUES (?,?,?,?)",(iid,total,(inv_date+timedelta(days=random.randint(0,5))).isoformat(),random.choice(pay_modes)))
    elif r<0.92:
        cur.execute("INSERT INTO payment(invoice_id,amount_paid,payment_date,payment_mode) VALUES (?,?,?,?)",(iid,round(total*0.5,2),inv_date.isoformat(),random.choice(pay_modes)))
    # else: unpaid
    return rid

rented_now=set()
open_count=0; overdue_count=0
for v in veh:
    cursor_day = date(2025,1,1)+timedelta(days=random.randint(0,40))
    while cursor_day < date(2026,12,15):
        length = random.randint(1,9)
        s, e = cursor_day, cursor_day+timedelta(days=length)
        cursor_day = e + timedelta(days=random.randint(1,25))
        branch = random.randint(1,len(branches))
        for _ in range(20):
            c=random.randint(1,70)
            if cust_exp[c] >= s: break
        else: continue
        if e < TODAY - timedelta(days=3):
            status = 'CANCELLED' if random.random()<0.08 else 'COMPLETED'
            mode='done'
        elif s <= TODAY <= e + timedelta(days=0) or (e < TODAY):
            # active now / just ended -> keep few open rentals
            if open_count<12:
                status='BOOKED'; mode='open'; open_count+=1
                if e < TODAY: overdue_count+=1
            else:
                status='COMPLETED'; mode='done'
                if e>=TODAY: continue
        else:
            status = 'CANCELLED' if random.random()<0.1 else 'BOOKED'; mode=None
        cur.execute("INSERT INTO reservation(customer_id,vehicle_id,branch_id,start_date,end_date,status,created_at) VALUES (?,?,?,?,?,?,?)",
                    (c,v,branch,s.isoformat(),e.isoformat(),status,(s-timedelta(days=random.randint(2,30))).isoformat()+" 10:00:00"))
        rsv=cur.lastrowid
        if mode and status!='CANCELLED':
            add_rental(rsv,v,branch,s,e,mode)
            if mode=='open': rented_now.add(v)

for v in rented_now:
    cur.execute("UPDATE vehicle SET status='RENTED' WHERE vehicle_id=?",(v,))
for v in random.sample([x for x in veh if x not in rented_now],8):
    cur.execute("UPDATE vehicle SET status='MAINTENANCE' WHERE vehicle_id=?",(v,))
con.commit()
for t in ["branch","customer","vehiclecategory","rateplan","vehicle","reservation","rental","damage","fuelreading","inspection","invoice","payment"]:
    print(f"{t:16}", cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
print("overdue open rentals:", cur.execute("""SELECT COUNT(*) FROM rental r JOIN reservation s ON s.reservation_id=r.reservation_id
      WHERE r.return_date IS NULL AND s.end_date < date('2026-10-05')""").fetchone()[0])
con.close()
