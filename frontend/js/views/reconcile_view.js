export const reconcileViewTemplate = `
  <div v-show="tab===4" class="panel">
    <h2>对账互证</h2>
    <p :class="recon.all_ok ? 'ok' : 'err'">{{ recon.all_ok ? '全部同源' : '存在分裂' }}</p>
    <p :class="recon.renew_all_ok ? 'ok' : 'err'">
      续期同源灯：{{ recon.renew_all_ok ? '正式行时刻与续期审计同源' : ('分裂 ' + (recon.renew_mismatch || 0) + ' 笔：审计与正式行过期时刻不一致') }}
    </p>
    <table>
      <thead><tr><th>档</th><th>盘面已订</th><th>正式册席</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="i in (recon.items||[])" :key="i.slot_code">
          <td>{{ i.slot_code }}</td><td>{{ i.slot_booked }}</td>
          <td>{{ i.booking_seats }}</td>
          <td :class="i.ok?'ok':'err'">{{ i.ok ? '通过' : '分裂' }}</td>
        </tr>
      </tbody>
    </table>
    <h3>续期时刻对账</h3>
    <table>
      <thead><tr><th>占位ID</th><th>档</th><th>正式行过期时刻</th><th>审计 new_expire</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="r in (recon.renew_items||[])" :key="r.hold_id">
          <td>{{ r.hold_id }}</td><td>{{ r.slot_code }}</td>
          <td>{{ r.expire_at }}</td><td>{{ r.audit_new_expire }}</td>
          <td :class="r.renew_ok?'ok':'err'">{{ r.renew_ok ? '同源' : '分裂' }}</td>
        </tr>
        <tr v-if="!(recon.renew_items||[]).length"><td colspan="5">暂无续期记录</td></tr>
      </tbody>
    </table>
  </div>
`;
