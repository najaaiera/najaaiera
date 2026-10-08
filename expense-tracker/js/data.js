/* Static reference data: categories, account types, currencies. */
window.TRACKER_DATA = (function () {
  'use strict';

  // Category → sub-categories, per transaction type.
  const CATEGORIES = {
    expense: [
      { id: 'food', label: 'Food & Dining', icon: '🍽️', subs: ['Groceries', 'Restaurants', 'Coffee & Snacks', 'Food Delivery'] },
      { id: 'transport', label: 'Transport', icon: '🚗', subs: ['Fuel', 'Toll', 'Parking', 'Public Transport', 'E-hailing', 'Car Maintenance'] },
      { id: 'utilities', label: 'Utilities', icon: '💡', subs: ['Electricity', 'Water', 'Internet', 'Mobile', 'Gas'] },
      { id: 'housing', label: 'Housing', icon: '🏠', subs: ['Rent', 'Mortgage', 'Repairs', 'Furniture'] },
      { id: 'shopping', label: 'Shopping', icon: '🛍️', subs: ['Clothing', 'Electronics', 'Household', 'Personal Care'] },
      { id: 'health', label: 'Health', icon: '💊', subs: ['Clinic', 'Pharmacy', 'Insurance', 'Fitness'] },
      { id: 'leisure', label: 'Leisure', icon: '🎬', subs: ['Movies', 'Subscriptions', 'Hobbies', 'Travel'] },
      { id: 'education', label: 'Education', icon: '📚', subs: ['Tuition', 'Books', 'Courses'] },
      { id: 'family', label: 'Family & Giving', icon: '🤝', subs: ['Childcare', 'Gifts', 'Donations', 'Zakat'] },
      { id: 'financial', label: 'Financial', icon: '🧾', subs: ['Bank Fees', 'Loan Repayment', 'Credit Card Bill', 'Tax'] },
      { id: 'other-exp', label: 'Other', icon: '📦', subs: ['Miscellaneous'] }
    ],
    income: [
      { id: 'salary', label: 'Salary', icon: '💼', subs: ['Monthly Salary', 'Overtime', 'Bonus', 'Allowance'] },
      { id: 'business', label: 'Business', icon: '🏪', subs: ['Sales', 'Freelance', 'Commission'] },
      { id: 'investment', label: 'Investment', icon: '📈', subs: ['Dividends', 'Interest', 'Capital Gains'] },
      { id: 'refunds', label: 'Gifts & Refunds', icon: '🎁', subs: ['Gift', 'Refund', 'Cashback', 'Rebate'] },
      { id: 'other-inc', label: 'Other', icon: '💰', subs: ['Miscellaneous'] }
    ],
    transfer: [
      { id: 'own-transfer', label: 'Own Transfer', icon: '🔁', subs: ['Between Own Accounts'] },
      { id: 'savings', label: 'Savings', icon: '🐖', subs: ['Emergency Fund', 'Fixed Deposit', 'Goal Savings', 'ASB / Unit Trust'] },
      { id: 'topup', label: 'E-wallet Top-up', icon: '📲', subs: ['Reload', 'Auto Top-up'] },
      { id: 'cash', label: 'Cash Withdrawal', icon: '🏧', subs: ['ATM', 'Over the Counter'] },
      { id: 'card-payment', label: 'Credit Card Payment', icon: '💳', subs: ['Full Payment', 'Minimum Payment'] }
    ]
  };

  const ACCOUNT_TYPES = [
    { id: 'bank', label: 'Bank', icon: '🏦' },
    { id: 'ewallet', label: 'E-wallet', icon: '📱' },
    { id: 'cash', label: 'Cash', icon: '💵' },
    { id: 'card', label: 'Credit Card', icon: '💳' },
    { id: 'savings', label: 'Savings', icon: '🐖' }
  ];

  // Seeded on first run — users can rename, edit or delete them.
  const DEFAULT_ACCOUNTS = [
    { name: 'Maybank', type: 'bank' },
    { name: 'CIMB', type: 'bank' },
    { name: "Touch 'n Go", type: 'ewallet' },
    { name: 'Cash', type: 'cash' }
  ];

  const CURRENCIES = ['MYR', 'SGD', 'IDR', 'THB', 'PHP', 'BND', 'USD', 'EUR', 'GBP', 'AUD', 'JPY', 'CNY', 'INR'];

  return { CATEGORIES, ACCOUNT_TYPES, DEFAULT_ACCOUNTS, CURRENCIES };
})();
