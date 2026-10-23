import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../store.dart';
import 'home_screen.dart' show categoryIcon;

/// Spending per category and per day for one month.
class SummaryScreen extends StatefulWidget {
  const SummaryScreen({super.key, required this.store, required this.month});

  final ExpenseStore store;
  final DateTime month;

  @override
  State<SummaryScreen> createState() => _SummaryScreenState();
}

class _SummaryScreenState extends State<SummaryScreen> {
  late DateTime _month = DateTime(widget.month.year, widget.month.month);

  void _shift(int delta) =>
      setState(() => _month = DateTime(_month.year, _month.month + delta));

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final total = store.totalForMonth(_month);
    final cats = store.categoryTotals(_month).entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final daysInMonth = DateTime(_month.year, _month.month + 1, 0).day;
    final now = DateTime.now();
    final isCurrent = now.year == _month.year && now.month == _month.month;
    final daysCounted = isCurrent ? now.day : daysInMonth;
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('Monthly summary')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              IconButton(
                  onPressed: () => _shift(-1),
                  icon: const Icon(Icons.chevron_left)),
              Expanded(
                child: Text(DateFormat('MMMM yyyy').format(_month),
                    textAlign: TextAlign.center,
                    style: theme.textTheme.titleLarge),
              ),
              IconButton(
                  onPressed: () => _shift(1),
                  icon: const Icon(Icons.chevron_right)),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              _stat('Total', store.money(total)),
              const SizedBox(width: 12),
              _stat('Daily average', store.money(total / daysCounted)),
            ],
          ),
          const SizedBox(height: 24),
          Text('By category', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          if (cats.isEmpty)
            Padding(
              padding: const EdgeInsets.all(24),
              child: Center(
                  child: Text('No expenses this month.',
                      style: TextStyle(color: theme.colorScheme.outline))),
            ),
          for (final c in cats)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                children: [
                  Icon(categoryIcon(c.key), size: 20),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(children: [
                          Expanded(child: Text(c.key)),
                          Text(
                              '${store.money(c.value)}  (${(c.value / total * 100).toStringAsFixed(0)}%)'),
                        ]),
                        const SizedBox(height: 4),
                        LinearProgressIndicator(
                          value: total == 0 ? 0 : c.value / total,
                          minHeight: 8,
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _stat(String label, String value) => Expanded(
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label),
                const SizedBox(height: 4),
                Text(value,
                    style: Theme.of(context)
                        .textTheme
                        .titleLarge
                        ?.copyWith(fontWeight: FontWeight.bold)),
              ],
            ),
          ),
        ),
      );
}
