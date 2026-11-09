# Retrieval evaluation

Generated 2026-11-09T12:30:25+00:00; top_k=10; 20 questions. A hit is relevant if it comes from the expected file and contains the key phrase (case/whitespace-insensitive).

## Summary

| config | recall@1 | recall@3 | recall@5 | recall@10 | mrr | ndcg@10 |
|---|---|---|---|---|---|---|
| vector | 0.650 | 0.750 | 0.850 | 0.900 | 0.723 | 0.765 |
| hybrid | 0.700 | 0.800 | 0.900 | 0.900 | 0.772 | 0.804 |
| hybrid_rerank | 0.800 | 0.900 | 1.000 | 1.000 | 0.858 | 0.893 |

Configs: vector: bm25=False, reranker=False; hybrid: bm25=True, reranker=False; hybrid_rerank: bm25=True, reranker=True

## Per-question first relevant rank

| # | question | bid | expected file | key phrase | vector | hybrid | hybrid_rerank |
|---|---|---|---|---|---|---|---|
| 1 | What is the new due date? | Bid1 | Addendum 2 RFP JA-207652 Student and Staff Computing Devices.pdf | July 9, 2024 | 1 | 1 | 1 |
| 2 | When was the original solicitation due? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | 27-JUN-2024 | miss | miss | 3 |
| 3 | Is there a pre-proposal meeting? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | TEAMS video conference | 4 | 1 | 1 |
| 4 | How long is the contract term? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | two (2) successive one (1) year | 1 | 1 | 1 |
| 5 | Must Form 1295 be submitted with the response? | Bid1 | Addendum 1 RFP JA-207652 Student and Staff Computing Devices.pdf | submitted with the RFP response | 1 | 1 | 1 |
| 6 | Is etching required on monitors? | Bid1 | Addendum 1 RFP JA-207652 Student and Staff Computing Devices.pdf | only require etching on Laptops | 1 | 1 | 1 |
| 7 | Tier 3 student Windows laptop memory requirement | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | 16GB RAM DDR5 | 1 | 1 | 1 |
| 8 | How many Tier 1 Chromebooks are estimated? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | 50,000 | 1 | 1 | 1 |
| 9 | Who is the buyer? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | JALZATE@dallasisd.org | miss | 2 | 3 |
| 10 | Which cooperatives can ride the contract? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | EPCNT | 1 | 1 | 1 |
| 11 | Can bids be emailed? | Bid1 | JA-207652 Student and Staff Computing Devices FINAL.pdf | No fax or email submissions | 3 | miss | 1 |
| 12 | What is the PORFP number? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | E20P4600040 | 1 | 1 | 1 |
| 13 | What is the closing time? | Bid2 | Dell Laptops w_Extended Warranty - Bid Information - {3} _ BidNet Direct.html | 06/10/2024 02:00 PM EDT | 6 | 5 | 4 |
| 14 | How soon must equipment be delivered? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | within 45 days of Award | 2 | 2 | 1 |
| 15 | Which affidavit concerns mercury? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | Mercury Affidavit | 5 | 4 | 4 |
| 16 | What processor does the Latitude 5550 have? | Bid2 | Dell_Laptop_Specs.pdf | Core Ultra 5 125U | 1 | 1 | 1 |
| 17 | Which master contract applies? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | 060B5400007 | 1 | 1 | 1 |
| 18 | How long is the extended warranty? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | 3 Years | 1 | 1 | 1 |
| 19 | WD22TB4 quantity | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | WD22TB4 | 1 | 1 | 1 |
| 20 | How are invoices submitted? | Bid2 | PORFP_-_Dell_Laptop_Final.pdf | STOaccountspayable | 1 | 1 | 1 |
