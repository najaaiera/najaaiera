import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'models.dart';

/// Keeps all expenses, day notes and settings in memory and saves them
/// to a JSON file in the app's documents folder. Receipt images are copied
/// into a `receipts` folder next to it, so they survive if the original
/// photo is deleted from the gallery.
class ExpenseStore extends ChangeNotifier {
  ExpenseStore({Directory? baseDir}) : _baseDirOverride = baseDir;

  final Directory? _baseDirOverride;
  late Directory _dir;

  final List<Expense> _expenses = [];
  final Map<String, String> _dayNotes = {};
  String currency = 'RM';

  List<Expense> get expenses => List.unmodifiable(_expenses);

  File get _dataFile => File('${_dir.path}${Platform.pathSeparator}data.json');
  Directory get _receiptDir =>
      Directory('${_dir.path}${Platform.pathSeparator}receipts');

  Future<void> load() async {
    _dir = _baseDirOverride ??
        Directory(
            '${(await getApplicationDocumentsDirectory()).path}${Platform.pathSeparator}DailyExpense');
    await _receiptDir.create(recursive: true);
    if (await _dataFile.exists()) {
      final data =
          jsonDecode(await _dataFile.readAsString()) as Map<String, dynamic>;
      _expenses
        ..clear()
        ..addAll((data['expenses'] as List? ?? [])
            .map((e) => Expense.fromJson(e as Map<String, dynamic>)));
      _dayNotes
        ..clear()
        ..addAll((data['dayNotes'] as Map? ?? {}).cast<String, String>());
      currency = (data['currency'] as String?) ?? 'RM';
    }
    notifyListeners();
  }

  Future<void> _save() async {
    final data = {
      'currency': currency,
      'expenses': _expenses.map((e) => e.toJson()).toList(),
      'dayNotes': _dayNotes,
    };
    // Write to a temp file first so a crash mid-write can't corrupt the data.
    final tmp = File('${_dataFile.path}.tmp');
    await tmp.writeAsString(jsonEncode(data), flush: true);
    await tmp.rename(_dataFile.path);
  }

  /// Copies a picked/snapped image into the app's receipt folder.
  Future<String> importReceipt(String sourcePath, String id) async {
    final dot = sourcePath.lastIndexOf('.');
    final ext = dot >= 0 ? sourcePath.substring(dot) : '.jpg';
    final dest =
        '${_receiptDir.path}${Platform.pathSeparator}${id}_${DateTime.now().millisecondsSinceEpoch}$ext';
    await File(sourcePath).copy(dest);
    return dest;
  }

  Future<void> upsert(Expense e) async {
    final i = _expenses.indexWhere((x) => x.id == e.id);
    if (i >= 0) {
      final old = _expenses[i];
      if (old.receiptPath != null && old.receiptPath != e.receiptPath) {
        await _deleteFile(old.receiptPath!);
      }
      _expenses[i] = e;
    } else {
      _expenses.add(e);
    }
    notifyListeners();
    await _save();
  }

  Future<void> delete(String id) async {
    final i = _expenses.indexWhere((x) => x.id == id);
    if (i < 0) return;
    final removed = _expenses.removeAt(i);
    if (removed.receiptPath != null) await _deleteFile(removed.receiptPath!);
    notifyListeners();
    await _save();
  }

  Future<void> _deleteFile(String path) async {
    try {
      final f = File(path);
      if (await f.exists()) await f.delete();
    } catch (_) {}
  }

  List<Expense> forDay(DateTime day) {
    final d = dayOnly(day);
    return _expenses.where((e) => dayOnly(e.date) == d).toList()
      ..sort((a, b) => b.date.compareTo(a.date));
  }

  List<Expense> forMonth(DateTime month) => _expenses
      .where((e) => e.date.year == month.year && e.date.month == month.month)
      .toList();

  double totalForDay(DateTime day) =>
      forDay(day).fold(0, (sum, e) => sum + e.amount);

  double totalForMonth(DateTime month) =>
      forMonth(month).fold(0, (sum, e) => sum + e.amount);

  Map<String, double> categoryTotals(DateTime month) {
    final m = <String, double>{};
    for (final e in forMonth(month)) {
      m[e.category] = (m[e.category] ?? 0) + e.amount;
    }
    return m;
  }

  String dayNote(DateTime day) => _dayNotes[dayKey(day)] ?? '';

  bool hasDayNote(DateTime day) => _dayNotes.containsKey(dayKey(day));

  Future<void> setDayNote(DateTime day, String text) async {
    final t = text.trim();
    if (t.isEmpty) {
      _dayNotes.remove(dayKey(day));
    } else {
      _dayNotes[dayKey(day)] = t;
    }
    notifyListeners();
    await _save();
  }

  Future<void> setCurrency(String c) async {
    currency = c.trim().isEmpty ? 'RM' : c.trim();
    notifyListeners();
    await _save();
  }

  String money(double v) => '$currency ${v.toStringAsFixed(2)}';
}
