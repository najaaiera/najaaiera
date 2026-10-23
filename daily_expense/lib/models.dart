/// Data classes for the app: an [Expense] and a free-text [DayNote].
library;

const List<String> kCategories = [
  'Food',
  'Transport',
  'Shopping',
  'Bills',
  'Health',
  'Entertainment',
  'Other',
];

/// Strips the time part so dates can be compared/grouped by day.
DateTime dayOnly(DateTime d) => DateTime(d.year, d.month, d.day);

/// "2026-10-23" — used as the key for day notes.
String dayKey(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

class Expense {
  final String id;
  final String title;
  final double amount;
  final String category;
  final DateTime date;
  final String note;

  /// Absolute path of the receipt image saved in the app folder, if any.
  final String? receiptPath;

  const Expense({
    required this.id,
    required this.title,
    required this.amount,
    required this.category,
    required this.date,
    this.note = '',
    this.receiptPath,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'amount': amount,
        'category': category,
        'date': date.toIso8601String(),
        'note': note,
        'receiptPath': receiptPath,
      };

  factory Expense.fromJson(Map<String, dynamic> j) => Expense(
        id: j['id'] as String,
        title: (j['title'] as String?) ?? '',
        amount: (j['amount'] as num).toDouble(),
        category: (j['category'] as String?) ?? 'Other',
        date: DateTime.parse(j['date'] as String),
        note: (j['note'] as String?) ?? '',
        receiptPath: j['receiptPath'] as String?,
      );
}
