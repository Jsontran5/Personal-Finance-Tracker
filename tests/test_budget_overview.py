from unittest import TestCase

from finance.budgeting import monthly_summary


class MonthlySummaryTests(TestCase):
    def test_refunds_unknowns_exclusions_and_savings_expenses(self):
        def tx(kind, amount, category=None, **extra):
            return dict(kind=kind,amount_minor=amount,category_id=category,pending=0,needs_review=0,**extra)
        rows=[tx('income',100000),tx('expense',20000,1),tx('refund',5000,1),
              tx('expense',10000,2),tx('expense',3000),tx('expense',2000,3),
              tx('transfer',40000),tx('investment',10000),tx('income',50000,transfer_pair_id=1)]
        pending=tx('expense',9000,1);pending['pending']=1;rows.append(pending)
        review=tx('income',80000);review['needs_review']=1;rows.append(review)
        categories=[dict(id=i,bucket=b) for i,b in [(1,'needs'),(2,'wants'),(3,'savings')]]
        result=monthly_summary(rows,categories,[50,30,20])
        self.assertEqual(result['income_minor'],100000)
        self.assertEqual(result['spending_minor'],30000)
        self.assertEqual(result['surplus_minor'],70000)
        self.assertEqual(result['surplus_rate'],70)
        self.assertEqual(result['buckets'],dict(needs=15000,wants=10000,savings=2000,uncategorized=3000))
        self.assertEqual(result['excluded_count'],2)

    def test_no_income_and_net_refund_are_not_clamped(self):
        rows=[dict(kind='refund',amount_minor=2000,category_id=None,pending=0,needs_review=0)]
        result=monthly_summary(rows,[],[50,30,20])
        self.assertIsNone(result['surplus_rate'])
        self.assertEqual(result['spending_minor'],-2000)
        self.assertEqual(result['surplus_minor'],2000)
