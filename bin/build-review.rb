#!/usr/bin/env ruby
# frozen_string_literal: true

require 'base64'
require 'cgi'
require 'erb'
require 'json'
require 'fileutils'

abort "usage: #{$PROGRAM_NAME} IMAGE NDL_JSON PADDLE_JSON OUTPUT_HTML" unless ARGV.length == 4
image_path, ndl_path, paddle_path, output_path = ARGV

def box_for(line)
  points = line.fetch('boundingBox')
  xs = points.map(&:first)
  ys = points.map(&:last)
  [xs.min, ys.min, xs.max, ys.max]
end

def overlap_ratio(a, b)
  width = [[a[2], b[2]].min - [a[0], b[0]].max, 0].max
  height = [[a[3], b[3]].min - [a[1], b[1]].max, 0].max
  areas = [a, b].map { |box| (box[2] - box[0]) * (box[3] - box[1]) }
  width * height.to_f / areas.min
end

def same_column?(a, b)
  x_overlap = [a[2], b[2]].min - [a[0], b[0]].max
  y_overlap = [a[3], b[3]].min - [a[1], b[1]].max
  x_overlap >= 0.5 * [a[2] - a[0], b[2] - b[0]].min &&
    y_overlap >= 0.2 * [a[3] - a[1], b[3] - b[1]].min
end

def highlighted_pair(left, right)
  a = left.scan(/\X/)
  b = right.scan(/\X/)
  matrix = Array.new(a.length + 1) { Array.new(b.length + 1, 0) }
  (0..a.length).each { |i| matrix[i][0] = i }
  (0..b.length).each { |j| matrix[0][j] = j }
  a.each_with_index do |char, ai|
    b.each_with_index do |other, bi|
      i = ai + 1
      j = bi + 1
      matrix[i][j] = [matrix[i - 1][j] + 1, matrix[i][j - 1] + 1,
                      matrix[i - 1][j - 1] + (char == other ? 0 : 1)].min
    end
  end

  operations = []
  i = a.length
  j = b.length
  until i.zero? && j.zero?
    if i.positive? && j.positive? &&
       matrix[i][j] == matrix[i - 1][j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1)
      operations << [a[i - 1] == b[j - 1] ? :same : :replace, a[i - 1], b[j - 1]]
      i -= 1
      j -= 1
    elsif i.positive? && matrix[i][j] == matrix[i - 1][j] + 1
      operations << [:delete, a[i - 1], nil]
      i -= 1
    else
      operations << [:insert, nil, b[j - 1]]
      j -= 1
    end
  end

  rendered = [String.new, String.new]
  in_change = false
  operations.reverse_each do |kind, old_char, new_char|
    if kind == :same && in_change
      rendered.each { |side| side << '</mark>' }
      in_change = false
    elsif kind != :same && !in_change
      rendered.each { |side| side << '<mark>' }
      in_change = true
    end
    rendered[0] << CGI.escapeHTML(old_char) if old_char
    rendered[1] << CGI.escapeHTML(new_char) if new_char
  end
  rendered.each { |side| side << '</mark>' } if in_change
  [matrix[a.length][b.length], *rendered]
end

ndl_data = JSON.parse(File.read(ndl_path))
ndl = ndl_data.fetch('contents').flatten
paddle_data = JSON.parse(File.read(paddle_path))
paddle = paddle_data.fetch('rec_texts').each_with_index.map do |text, index|
  { text: text, box: paddle_data.fetch('rec_boxes').fetch(index) }
end
xml_path = ndl_path.sub(/\.json\z/, '.xml')
blocks = if File.file?(xml_path)
           File.read(xml_path).scan(/<BLOCK\b([^>]+)\/>/).map do |raw|
             attrs = raw.first.scan(/([A-Z]+)="([^"]*)"/).to_h
             x = attrs.fetch('X').to_i
             y = attrs.fetch('Y').to_i
             { type: attrs.fetch('TYPE'), box: [x, y,
               x + attrs.fetch('WIDTH').to_i, y + attrs.fetch('HEIGHT').to_i] }
           end
         else
           []
         end

matched = Hash.new { |hash, key| hash[key] = [] }
unmatched = []
paddle.each do |line|
  choices = ndl.each_with_index.filter_map do |candidate, index|
    next unless same_column?(box_for(candidate), line.fetch(:box))

    a = box_for(candidate)
    [index, ((a[0] + a[2]) / 2.0 - (line.fetch(:box)[0] + line.fetch(:box)[2]) / 2.0).abs]
  end
  choices.empty? ? unmatched << line : matched[choices.min_by(&:last).first] << line
end

rows = ndl.each_with_index.map do |line, index|
  parts = matched[index].sort_by { |part| part.fetch(:box)[1] }
  ndl_text = line.fetch('text')
  paddle_text = parts.map { |part| part.fetch(:text) }.join
  distance, ndl_html, paddle_html = highlighted_pair(ndl_text, paddle_text)
  { index: index + 1, box: box_for(line), distance: distance,
    ndl_html: ndl_html, paddle_html: paddle_html, paddle_missing: parts.empty? }
end

extras = unmatched.map do |line|
  block = blocks.max_by { |candidate| overlap_ratio(candidate.fetch(:box), line.fetch(:box)) }
  type = block && overlap_ratio(block.fetch(:box), line.fetch(:box)) >= 0.5 ? block.fetch(:type) : '未分類'
  { type: type, text: line.fetch(:text), box: line.fetch(:box) }
end
extras.sort_by! { |item| [item.fetch(:box)[0] * -1, item.fetch(:box)[1]] }

page = File.basename(image_path)[/page-(\d+)/, 1] || '?'
image_data = Base64.strict_encode64(File.binread(image_path))
image_width = ndl_data.fetch('imginfo').fetch('img_width')
image_height = ndl_data.fetch('imginfo').fetch('img_height')
total_edits = rows.sum { |row| row.fetch(:distance) }
template = File.read(File.expand_path('../templates/review.html.erb', __dir__))
FileUtils.mkdir_p(File.dirname(output_path))
File.write(output_path, ERB.new(template).result(binding))
puts "wrote #{output_path}: #{rows.length} columns, #{total_edits} OCR differences, #{extras.length} other regions"
