package com.mlw.zongce.ui.calculator

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mlw.zongce.data.CalcStateStore
import com.mlw.zongce.logic.CalcActions
import com.mlw.zongce.logic.CalcState
import com.mlw.zongce.logic.CourseField
import com.mlw.zongce.logic.CrawlOutcome
import com.mlw.zongce.logic.GradeImport
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * 计算器状态容器：加载 → 内存态 → 每次变更落盘。
 * 全部业务规则在 [CalcActions]（纯函数），本类只负责加载与持久化时序。
 */
class CalculatorViewModel(private val store: CalcStateStore) : ViewModel() {

    /** null = 历史数据加载中 */
    private val _state = MutableStateFlow<CalcState?>(null)
    val state: StateFlow<CalcState?> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            _state.value = store.state.first()
        }
    }

    fun onCourseInput(index: Int, field: CourseField, text: String) =
        update { CalcActions.inputCourse(it, index, field, text) }

    fun onDeleteCourse(index: Int) =
        update { CalcActions.deleteCourse(it, index) }

    fun onAddCourse() = update { CalcActions.addCourse(it) }

    fun onFieldChange(key: String, text: String) =
        update { CalcActions.setField(it, key, text) }

    fun onReset() = update { CalcActions.reset() }

    /** 抓取结果导入课程行（对应网页版 importGradeRows） */
    fun importGrades(outcome: CrawlOutcome) = update { GradeImport.importInto(it, outcome) }

    private fun update(transform: (CalcState) -> CalcState) {
        val current = _state.value ?: return
        val next = transform(current)
        _state.value = next
        viewModelScope.launch { store.save(next) }
    }
}
