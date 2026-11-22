export const renewViewTemplate = `
  <div v-show="tab===6" class="panel">
    <h2>续期（倒计时与正式行同源）</h2>
    <p class="sk">倒计时仅按接口返回的正式占位行 expire_at 计算；续期成功后整页同源刷新。</p>
    <p class="err" v-if="actionErr">{{ actionErr }}</p>
    <table>
      <thead>
        <tr><th>ID</th><th>档</th><th>当事人</th><th>态</th><th>正式过期时刻</th><th>倒计时</th><th>续期</th></tr>
      </thead>
      <tbody>
        <tr v-for="h in holds" :key="h.id">
          <td>{{ h.id }}</td>
          <td>{{ h.slot_code }}</td>
          <td>{{ h.party_key }}</td>
          <td>{{ h.status }}</td>
          <td>{{ h.expire_at }}</td>
          <td :class="countdown(h.expire_at).late ? 'err' : ''">{{ countdown(h.expire_at).text }}</td>
          <td>
            <button v-if="h.status==='占位中'" @click="doRenew(h.id, 30)">续 30 分</button>
            <button class="ghost" v-if="h.status==='占位中'" @click="doRenew(h.id, 60)">续 60 分</button>
          </td>
        </tr>
      </tbody>
    </table>

    <h3>占位详情</h3>
    <div>
      <input v-model.number="detailId" type="number" placeholder="占位 ID" style="width:7rem" />
      <button class="ghost" @click="loadHoldDetail">查详情</button>
    </div>
    <table v-if="holdDetail">
      <tbody>
        <tr><th>ID</th><td>{{ holdDetail.id }}</td></tr>
        <tr><th>档期</th><td>{{ holdDetail.slot_code }}</td></tr>
        <tr><th>当事人</th><td>{{ holdDetail.party_key }}</td></tr>
        <tr><th>状态</th><td>{{ holdDetail.status }}</td></tr>
        <tr><th>过期时刻（正式行）</th><td>{{ holdDetail.expire_at }}</td></tr>
        <tr><th>倒计时</th><td :class="countdown(holdDetail.expire_at).late ? 'err' : ''">{{ countdown(holdDetail.expire_at).text }}</td></tr>
      </tbody>
    </table>

    <h3>续期审计（同源 audit_trail / action=hold_renew）</h3>
    <table>
      <thead><tr><th>ID</th><th>人</th><th>占位</th><th>载荷</th><th>时</th></tr></thead>
      <tbody>
        <tr v-for="a in renewAudits" :key="a.id">
          <td>{{ a.id }}</td><td>{{ a.actor }}</td><td>{{ a.target }}</td>
          <td>{{ a.payload }}</td><td>{{ a.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
