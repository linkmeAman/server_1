# Database Table Schemas

## Main DB

### `contact`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK AUTO_INCREMENT | |
| `contact_group_id` | int | 1=inquiry, 2=employee, 4=Member, 8=Franchise Inquiry |
| `fname` | varchar(30) | |
| `mname` | varchar(50) | |
| `lname` | varchar(30) | |
| `parent_name` | text | |
| `nick_name` | varchar(30) | |
| `country_code` | varchar(5) | default '+91' |
| `mobile` | varchar(20) | primary mobile |
| `country_code_2` | varchar(5) | |
| `mobile2` | varchar(20) | secondary mobile |
| `phone_no` | varchar(15) | |
| `email` | varchar(50) | |
| `personal_email` | varchar(50) | |
| `blogger` | int | |
| `gender` | varchar(8) | |
| `image` | varchar(30) | |
| `dob` | date | |
| `marital_status` | int | 0=Unmarried, 1=Married |
| `formal_title` | varchar(10) | |
| `father_name` | varchar(100) | |
| `spouse_name` | varchar(100) | |
| `age` | varchar(2) | |
| `occupation` | int | 1=Student, 0=Employee, 2=Business |
| `gstin` | varchar(30) | |
| `company` | varchar(100) | member's gstin |
| `company_add` | varchar(200) | member's company address |
| `cv` | varchar(50) | |
| `document_type_id` | int | contact_document id |
| `document_number` | varchar(50) | id proof number |
| `document_image` | varchar(200) | |
| `document_type_id_2` | int | |
| `document_number_2` | varchar(50) | |
| `document_image_2` | varchar(200) | |
| `document_type_id_3` | int | |
| `document_image_3` | varchar(200) | |
| `parent_id` | int | |
| `relation` | varchar(20) | |
| `address` | varchar(300) | |
| `pincode` | varchar(6) | |
| `city` | varchar(20) | |
| `state` | varchar(50) | |
| `country` | varchar(20) | |
| `dnd` | int | 0=Off, 1=On |
| `gcal_refresh_token` | varchar(100) | |
| `gcal_id` | varchar(100) | default 'primary' |
| `activity` | int | |
| `sleep` | int | in hours, default 7 |
| `stress` | int | |
| `smoking` | int | |
| `drinking` | int | |
| `drug` | int | |
| `carrot` | int | |
| `freshsales_id` | varchar(10) | |
| `quick_comment` | varchar(1000) | |
| `bid` | int | |
| `qualification_data` | json | |
| `event_flag` | int | 0=include, 1=exclude |
| `park` | int | soft-delete flag, default 0 |
| `created_by` | int | |
| `created_at` | datetime | |
| `modified_by` | int | |
| `modified_at` | timestamp | |

**Indexes:** `contact_group_id`, `document_type_id`, `bid`, `parent_id`, `fname`, `lname`, `mobile`, `park`, `doc_type_2`, `dob`

---

### `employee`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK AUTO_INCREMENT | |
| `contact_id` | int | FK → contact.id |
| `emergency_id` | int | |
| `ecode` | int | employee code |
| `department_id` | int | default 0 |
| `position_id` | int | FK → employee_position (central DB) |
| `interviewer` | int | 0=not eligible, 1=eligible |
| `master_trainer_contact_id` | int | |
| `grade` | int | default 1 |
| `counselor_status` | int | |
| `auto_assign_inq` | int | 0=false, 1=Online Form, 2=FB+Google, 3=Both |
| `auto_asign_folder` | int | |
| `doubleTick_acct_id` | int | |
| `qualifier` | int | |
| `associate` | int | |
| `doj` | date | date of joining |
| `notice_start_date` | date | |
| `doe` | date | |
| `exit_date` | date | |
| `handed_over` | int | 0=yes, 1=no |
| `on_notice` | int | |
| `status` | int | 1=active, 0=inactive |
| `user_account` | int | |
| `is_admin` | int | |
| `demo_owner` | int | |
| `workshift_id` | int | |
| `workshift_hours` | varchar(20) | |
| `workshift_in_time` | time | |
| `workshift_out_time` | time | |
| `week_off_code` | int | |
| `target_id` | int | |
| `salary_type` | int | 0=Basic,1=Fixed,2=per hour,3=per child,4=per hour(min.guarantee),5=Hybrid,6=per day,7=Full Base Sal |
| `salary` | int | basic salary amount |
| `tds_type` | int | 1=Fixed, 2=% |
| `tds_percent` | float(10,2) | |
| `rate_multiplier` | int | |
| `allowance` | decimal(20,2) | |
| `calculate_salary` | int | |
| `incentive_new` | varchar(11) | |
| `incentive_renew` | varchar(11) | |
| `p_incentive_c` | varchar(11) | |
| `p_incentive_sc` | varchar(11) | |
| `trainer_incentive` | int | |
| `mt_incentive` | int | |
| `mt_incentive_type` | int | |
| `rec_dir` | varchar(200) | |
| `type` | int | 0=Employee, 1=Freelancer |
| `invoice_payment` | int | |
| `assignment_check` | varchar(11) | |
| `assignment_no` | int | |
| `expense_balance` | int | |
| `multipleAccounts` | int | |
| `teacherContactId` | varchar(225) | Franchisee Teacher contact id |
| `park` | int | soft-delete flag, default 0 |
| `red_flag` | date | |
| `cash_collector` | int | |
| `created_by` | int | |
| `created_at` | datetime | |
| `modified_by` | int | |
| `modified_at` | timestamp | |
| `embedding` | json | |
| `is_parent` | int | default 0 |

**Indexes:** `contact_id`, `emergency_id`, `position_id`, `workshift_id`, `target_id`, `park`, `department_id`, `(id, status, park)`

> ⚠️ No `designation` or `department` text columns — use `department_id` and `position_id` (int FK refs).

---

## Central DB

### `user`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK AUTO_INCREMENT | |
| `fname` | varchar(50) | |
| `lname` | varchar(50) | |
| `admin` | int | default 0 |
| `mpin` | varchar(10) | |
| `password` | varchar(50) | |
| `client_id` | int | |
| `position_id` | int | FK → employee_position |
| `contact_id` | int | contact id from client_id's database |
| `chat_id` | varchar(100) | |
| `country_code` | varchar(5) | default '+91' |
| `mobile` | varchar(20) | |
| `type` | varchar(20) | |
| `is_first_login` | int | 1=first login, default 1 |
| `inactive` | int | default 0 |
| `park` | int | soft-delete flag, default 0 |
| `signup` | int | default 0 |
| `incomplete_alert` | int | 0=not shot, 1=shot, 2=member created in admin |
| `otp` | int | for registration |
| `system_id` | varchar(100) | |
| `last_changed_mpin` | datetime | |
| `last_changed_password` | datetime | |
| `created_at` | datetime | |
| `modified_at` | timestamp | |
| `password_hash` | varchar(255) | |
| `password_hash_algo` | varchar(32) | default 'bcrypt' |
| `password_hash_updated_at` | datetime | |

**Indexes:** `mobile`, `password`, `mpin`, `client_id`, `type`, `park`
