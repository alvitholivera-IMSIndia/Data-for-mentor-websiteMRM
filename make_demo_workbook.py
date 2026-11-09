"""Builds demo_MRM.xlsx that mimics the two pivot sheets (values copied from the screenshots)."""
import openpyxl

# city, centre, enrol, tgt_myplans, total_myplans, atleast1, mpe_tgt, cov_tgt, meets | tgt_sim, tot_sim, atleast1_sim, tpe_tgt
D = [
 ("Agra","Agra - Agra",24,39,48,22,1.64,.75,10,167,130,22,6.96),
 ("Bangalore","Bangalore - Electronic City",9,21,42,9,2.38,.80,1,76,33,6,8.43),
 ("Bangalore","Bangalore - Indiranagar",208,386,394,197,1.86,.78,104,1542,1052,140,7.41),
 ("Bangalore","Bangalore - Jayanagar",284,538,672,278,1.90,.78,117,2143,1531,202,7.54),
 ("Delhi","Delhi - NCR - Connaught Place",472,923,1357,422,2,.78,169,3580,2636,340,7.58),
 ("Delhi","Delhi - NCR - Kailash Colony",156,298,314,127,2,.79,80,1159,1151,129,7.43),
 ("Delhi","Delhi - NCR - North Campus",94,187,329,85,2,.79,34,720,569,73,7.66),
 ("Hyderabad","Hyderabad - IMS Manya - Taranaka",5,9,6,3,2,.80,3,38,12,3,7.59),
 ("Hyderabad","Hyderabad - Madhapur",294,507,440,243,2,.78,186,2115,1982,245,7.19),
 ("Hyderabad","Hyderabad - Narayanguda",117,212,192,102,2,.78,70,845,491,74,7.22),
 ("Hyderabad","Hyderabad - Punjagutta",174,321,225,140,2,.79,126,1284,896,132,7.38),
 ("Kolkata","Kolkata - Gariahat",129,288,283,125,2,.80,70,1056,748,105,8.19),
 ("Kolkata","Kolkata - Park Street",128,268,308,109,2,.79,70,1015,794,100,7.93),
 ("Kolkata","Kolkata - Saltlake",123,249,324,113,2,.79,53,951,855,96,7.73),
 ("Mumbai (East)","Mumbai - Mulund",28,75,51,20,3,.80,21,254,83,9,9.07),
 ("Mumbai (East)","Mumbai - Thane",411,857,504,285,2,.79,313,3231,1748,257,7.86),
]
wb = openpyxl.Workbook()
a = wb.active; a.title = "Checking"
b = wb.create_sheet("CheckingSim")
a.append(["City","Report City Centre Final","#Enrolments","Target myPlans","Total myPlans","At least one myPlan",
          "MPE Target","MPE Actual","Coverage Target","Coverage Actual","Oct myPlans","≥ myPlan Benchmark"])
b.append(["COCO / Non COCO","City","Report City Centre Final","#Enrolments","Target SimCATs","Total SimCATs",
          "At Least 1 SimCAT","TPE Target","TPE Actual"])
last = None
for city, c, e, tm, mp, a1, mt, ct, mb, ts, sm, s1, tt in D:
    a.append([city if city != last else None, c, e, tm, mp, a1, mt, round(mp/e,2), ct, round(a1/e,2), 0, mb])
    b.append(["COCO", city if city != last else None, c, e, ts, sm, s1, tt, round(sm/e,2)])
    last = city
a.append(["Grand Total", None, sum(d[2] for d in D)]); b.append([None, "Grand Total", None, sum(d[2] for d in D)])
wb.save("demo_MRM.xlsx")
