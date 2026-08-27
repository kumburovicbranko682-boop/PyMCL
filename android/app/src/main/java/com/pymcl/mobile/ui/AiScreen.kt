package com.pymcl.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pymcl.mobile.model.AiMessage

/**
 * AI 助手（Android）：后端尚未接入，界面明示禁用，不伪装成能聊天。
 * 桌面端（PySide6 / EziApp / WinUI）才有完整的 AI 助手能力。
 */
@Composable
fun AiScreen(
    messages: List<AiMessage>,
    onSend: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var input by rememberSaveable { mutableStateOf("") }
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("AI", style = MaterialTheme.typography.headlineMedium)
        Card(modifier = Modifier.fillMaxWidth()) {
            Text(
                "Android 端 AI 助手尚未接入，此页暂不可用。\n" +
                    "装游戏 / 装模组 / 崩溃分析等 AI 功能请使用桌面版 PyMCL。",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(12.dp),
            )
        }
        LazyColumn(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(messages, key = { "${it.timestamp}-${it.role}" }) { msg ->
                Text(
                    "${msg.role}: ${msg.content}",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        OutlinedTextField(
            value = input,
            onValueChange = { input = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("Android 端暂未接入 AI") },
            singleLine = true,
            enabled = false,
        )
        Button(
            onClick = {
                onSend(input)
                input = ""
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = false,
        ) {
            Text("发送（未接入）")
        }
    }
}
