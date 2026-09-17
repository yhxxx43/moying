`timescale 1ns / 1ps
// =====================================================================
// tb_morph3x3 —— 3x3 腐蚀/膨胀仿真验证
// 最小尺寸 W=8,H=4（WROW=2，一帧 8 字），软件算期望比对。
// =====================================================================
module tb_morph3x3;

    localparam W = 8, H = 4;
    localparam WROW = W/4;          // 2
    localparam WORDS = W*H/4;       // 8

    reg clk = 0;
    reg rstn = 0;
    reg [31:0] s_axis_tdata = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    reg s_axis_tlast = 0;
    reg [3:0] s_axis_tkeep = 4'hF;

    wire [31:0] er_m_data, di_m_data;
    wire er_m_valid, di_m_valid;
    reg m_axis_tready = 1;
    wire er_m_last, di_m_last;

    morph3x3 #(.W(W), .H(H), .OP(0)) erode (
        .clk(clk), .rstn(rstn),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
        .s_axis_tkeep(s_axis_tkeep),
        .m_axis_tdata(er_m_data), .m_axis_tvalid(er_m_valid),
        .m_axis_tready(m_axis_tready), .m_axis_tlast(er_m_last),
        .m_axis_tkeep()
    );
    morph3x3 #(.W(W), .H(H), .OP(1)) dilate (
        .clk(clk), .rstn(rstn),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
        .s_axis_tkeep(s_axis_tkeep),
        .m_axis_tdata(di_m_data), .m_axis_tvalid(di_m_valid),
        .m_axis_tready(m_axis_tready), .m_axis_tlast(di_m_last),
        .m_axis_tkeep()
    );

    always #5 clk = ~clk;

    // 诊断：打印纵向组合的输入
    always @(posedge clk) begin
        if (erode.col1 == 0 && erode.cur !== 32'h0 && erode.row2 !== 0)
            $display("t=%0t col1=0 row2=%0d r0h_d1=%h r1=%h r2=%h out_comb=%h",
                     $time, erode.row2, erode.r0_h_d1, erode.r1, erode.r2, erode.out_comb);
    end

    // 诊断：打印 valid 时序
    initial begin
        $monitor("t=%0t s_valid=%b s_ready=%b er_sr=%b er_v=%b",
                 $time, s_axis_tvalid, s_axis_tready, erode.valid_sr, er_m_valid);
    end

    // 输出采集
    reg [31:0] cap_er [0:31], cap_di [0:31];
    integer cap_cnt = 0;
    always @(posedge clk) begin
        if (er_m_valid && m_axis_tready) begin
            cap_er[cap_cnt] <= er_m_data;
            cap_di[cap_cnt] <= di_m_data;
            cap_cnt <= cap_cnt + 1;
        end
    end

    // 输入图（字节）：全 255，中间两个孤立 0
    reg [7:0] img [0:W*H-1];
    reg [7:0] exp_er [0:W*H-1];
    reg [7:0] exp_di [0:W*H-1];
    reg [31:0] in_words [0:WORDS-1];
    reg [31:0] exp_er_words [0:WORDS-1];
    reg [31:0] exp_di_words [0:WORDS-1];

    integer x, y, dx, dy, xx, yy, i, err;
    reg [7:0] p, acc;

    task send_frame;
        integer k;
        begin
            for (k = 0; k < WORDS; k = k + 1) begin
                s_axis_tdata = in_words[k];
                s_axis_tvalid = 1;
                s_axis_tlast = (k == WORDS-1);
                @(posedge clk);
            end
            s_axis_tvalid = 0;
            s_axis_tlast = 0;
        end
    endtask

    initial begin
        // 复位
        rstn = 0; repeat(10) @(posedge clk); rstn = 1; repeat(2) @(posedge clk);

        // 构造输入图
        for (i = 0; i < W*H; i = i + 1) img[i] = 8'd255;
        img[1*W + 2] = 8'd0;   // (2,1)
        img[1*W + 5] = 8'd0;   // (5,1)

        // 软件计算期望（3x3，边界填中性值）
        for (y = 0; y < H; y = y + 1) begin
            for (x = 0; x < W; x = x + 1) begin
                // 腐蚀（AND，边界填 255）
                acc = 8'hFF;
                for (dy = -1; dy <= 1; dy = dy + 1) begin
                    for (dx = -1; dx <= 1; dx = dx + 1) begin
                        xx = x + dx; yy = y + dy;
                        p = (xx < 0 || xx >= W || yy < 0 || yy >= H) ? 8'hFF : img[yy*W + xx];
                        acc = acc & p;
                    end
                end
                exp_er[y*W + x] = acc;
                // 膨胀（OR，边界填 0）
                acc = 8'h00;
                for (dy = -1; dy <= 1; dy = dy + 1) begin
                    for (dx = -1; dx <= 1; dx = dx + 1) begin
                        xx = x + dx; yy = y + dy;
                        p = (xx < 0 || xx >= W || yy < 0 || yy >= H) ? 8'h00 : img[yy*W + xx];
                        acc = acc | p;
                    end
                end
                exp_di[y*W + x] = acc;
            end
        end

        // 打包成 32bit 字
        for (i = 0; i < WORDS; i = i + 1) begin
            in_words[i]      = {img[4*i+3], img[4*i+2], img[4*i+1], img[4*i+0]};
            exp_er_words[i]  = {exp_er[4*i+3], exp_er[4*i+2], exp_er[4*i+1], exp_er[4*i+0]};
            exp_di_words[i]  = {exp_di[4*i+3], exp_di[4*i+2], exp_di[4*i+1], exp_di[4*i+0]};
        end

        // 送帧
        cap_cnt = 0;
        send_frame();
        repeat(20) @(posedge clk);   // 充分排空（输出延迟约 LATENCY 拍）

        // 比对
        err = 0;
        $display("DEBUG in0=%h [31:24]=%h [7:0]=%h", in_words[0], in_words[0][31:24], in_words[0][7:0]);
        $display("DEBUG exp0=%h [31:24]=%h [7:0]=%h", exp_er_words[0], exp_er_words[0][31:24], exp_er_words[0][7:0]);
        $display("DEBUG cap0=%h [31:24]=%h [7:0]=%h", cap_er[0], cap_er[0][31:24], cap_er[0][7:0]);
        if (cap_cnt != WORDS) begin
            $display("FAIL: 输出字数 %0d != %0d", cap_cnt, WORDS);
            err = err + 1;
        end else begin
            for (i = 0; i < WORDS; i = i + 1) begin
                if (cap_er[i] !== exp_er_words[i]) begin
                    $display("FAIL 腐蚀 word[%0d]: 期望 %h 实际 %h", i, exp_er_words[i], cap_er[i]);
                    err = err + 1;
                end
                if (cap_di[i] !== exp_di_words[i]) begin
                    $display("FAIL 膨胀 word[%0d]: 期望 %h 实际 %h", i, exp_di_words[i], cap_di[i]);
                    err = err + 1;
                end
            end
        end

        if (err == 0) $display("===== 全部 PASS =====");
        else $display("===== FAIL: %0d 处错误 =====", err);
        $finish;
    end

endmodule
