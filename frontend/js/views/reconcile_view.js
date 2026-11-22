export const reconcileViewTemplate = `
  <div v-show="tab===4" class="panel">
    <h2>对账互证</h2>
    <p :class="recon.all_ok ? 'ok' : 'err'">{{ recon.all_ok ? '全部同源' : '存在分裂' }}</p>
    <p v-if="(recon.renew_mismatches||[]).length" class="err">
      续期账实不一致：{{ (recon.renew_mismatches||[]).map(x => '#'+x.hold_id+' '+x.reason).join('；') }}
    </p>
    <p v-if="(recon.duplicate_active||[]).length" class="err">
      双有效席：{{ (recon.duplicate_active||[]).map(x => x.party_key+' x'+x.c).join('；') }}
    </p>
    <table>
      <thead><tr><th>档</th><th>盘面已订</th><th>正式册席</th><th>续期同源</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="i in (recon.items||[])" :key="i.slot_code">
          <td>{{ i.slot_code }}</td><td>{{ i.slot_booked }}</td>
          <td>{{ i.booking_seats }}</td>
          <td :class="i.renew_ok?'ok':'err'">{{ i.renew_ok ? '同源' : '分裂' }}</td>
          <td :class="i.ok?'ok':'err'">{{ i.ok ? '通过' : '分裂' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
