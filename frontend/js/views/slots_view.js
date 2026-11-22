export const slotsViewTemplate = `
  <div v-show="tab===0" class="panel">
    <h2>档期盘</h2>
    <div class="dash">
      <div class="cell"><div class="n">{{ dash.slots_open }}</div>开放档</div>
      <div class="cell"><div class="n">{{ dash.holds_open }}</div>占位中</div>
      <div class="cell"><div class="n">{{ dash.bookings }}</div>正式册</div>
      <div class="cell"><div class="n">{{ dash.waitlist }}</div>候补</div>
      <div class="cell" :class="dash.renew_mismatches ? 'bad' : ''"><div class="n">{{ dash.renew_mismatches }}</div>续期账实不符</div>
      <div class="cell" :class="dash.duplicate_active ? 'bad' : ''"><div class="n">{{ dash.duplicate_active }}</div>双有效席</div>
    </div>
    <table>
      <thead><tr><th>编码</th><th>标题</th><th>容量</th><th>已订</th><th>态</th><th>代</th><th>最近占位到期</th><th>续期</th></tr></thead>
      <tbody>
        <tr v-for="s in slots" :key="s.id">
          <td>{{ s.code }}</td><td>{{ s.title }}</td>
          <td>{{ s.capacity }}</td><td>{{ s.booked }}</td>
          <td>{{ s.status }}</td><td>{{ s.generation }}</td>
          <td>{{ s.next_hold_expire || '—' }}</td>
          <td :class="s.renew_ok?'ok':'err'">{{ s.renew_ok ? '同源' : '分裂' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
